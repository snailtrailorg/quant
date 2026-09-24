# 多账号源 D5 · 建任务与 worker 绑定（详细设计）

> 上级：`docs/architecture/A05-多账号源架构设计.md` §二·2.1/§三/§五契约8·9。状态：详细设计（待双盲审）。依赖：D1（account_allows）、D2（account_id）、D3（流键）。

## 一、目标

钉死「下单走哪条柜台链路」：建任务显式选源、三级时点权限校验、TD 网关按 account 构建（禁硬编码）、XTP 连接窗分支。

## 二、现状（已核实）

- `strategy_runner/main.py:199` 硬编码 `XTPAdapter(gateway=gw, ...)`——TD 网关不走 account 配置。
- `main.py:384` 读 `strategy_account.broker_provider`（自由文本）仅打日志，**从未驱动选源（死列）**；选源实为 `main.py:199` 硬编码 `XTPAdapter`——身份线未收编到 account。
- `perm_registry.py:56` market_op 五键下单卡口（role 级，非 account 级）。
- XTP 每日连接窗（`system_config` `xtp_session_lead_min`/`lag_min`）只对 A股 XTP 生效；EMT/加密不套（31 号 §五契约9）。

## 三、设计决策

- **无首决**。选源/校验/TD 网关方向已在 31 号定，本件落成契约 + 接入位。

## 四、契约

- **选源**：TD 人工显式选源（建任务 Web 选 account_id）；候选收窄到 **enabled + capabilities ∋ trading**（加密 account 无 quote，不得 trading+quote 收窄）。
- **三级时点**：①建任务拒绝（`account_allows`）②worker 启动 fail-fast ③下单前拦截（`risk_control.check_order` 前置）。**③时点接线：order dict 增 `account_id` 键 → place_order 传 account_id → check_order 用 account_id 调 account_allows + per-account `_get_global_state`（现 check_order account 恒空串、order 无 account_id，不钉则③静默失效）**。③时点 account_allows 仅拦 BUY/开仓，SELL 豁免（见 D1）。
- **TD 网关 per-account 构建**：按 account 的 provider+凭证构建网关/适配器，**禁硬编码 XTPAdapter/全局单例**；`query_position`/`query_account`/`order`/对账全走该 account 会话。**硬约束：构建必须 `build_xtp_setting(row_id=account_id)`（或等价），row_id=None / .env fallback 一律 EX_CONFIG fail-fast**——现 `main.py:137 _build_xtp_setting(client_id=...)` 未传 row_id、`broker.py:75 get_broker` 缺省取「域内 position 首行」，多 XTP account 下第二个 worker 会连到首行账户（A 任务下单落 B 账户）。
- **XTP 连接窗分支**：只 A股 XTP 套每日连接窗（lead/lag）；EMT/加密不套（常连）。
- **加密 stub 硬闸（沿用既有，不重造）**：加密 account 若 is_live 且 adapter 为 stub，fail-fast exit 78（批 6b 已实现，本件只引用）。

> **编码实施补充（2026-09-24，AB 双盲审后）**
> - **TD 网关 per-account 用注册表**（非 if provider== 硬编码，M3 守门立法）：`_TD_BUILDERS = {"xtp": _build_xtp_runtime}`（main.py 模块级），`_build_xtp_runtime` 抽 XTP 专属组装（ThinTdGateway + XtpTdApi + XTPAdapter + `build_xtp_setting(row_id=account_id)` 修串账户 bug）。非 XTP（builder None）走 stub + 硬闸。
> - **加密 stub 硬闸 D5 自己落**（文档原说「批 6b 已实现」是假的，代码无此闸）：非 XTP + 总闸开 + 该 provider 分项开 → `sys.exit(EX_CONFIG)`。
> - **fail-fast 异常捕获**（双盲审 A/B P0）：`get_interface_row`/`build_xtp_setting`/`create_adapter` 的 raise 包 try/except → `sys.exit(EX_CONFIG)`，否则 exit 1 触发 systemd on-failure 重启风暴（RestartPreventExitStatus 只豁免 78）。
> - **非 XTP `td_window=None`**（非 `(None,None)`）——`_td_connect_due` 首行 `if not win` 短路常连。

## 五、限定范围

只做：建任务选源 + 三级时点接入 + TD 网关 per-account 构建 + XTP 连接窗分支。
不碰：D1（account_allows 实现，只调用）、D2（account_id schema，只消费）、D3（流键，只消费）、D4（金融面）、D6（hub 部署）。

## 六、验收标准（编码阶段定，行为级）

- 建任务候选不含无 trading 能力的 account。
- 建任务选无权限品种的 account 被拒；worker 启动 fail-fast；下单前拦截（含 SELL 豁免、③时点 account_id 传入）。
- TD 网关按 account.provider 构建（EMQ/EMT account 不再走硬编码 XTPAdapter）；**row_id=account_id 显式传入，None/.env fallback fail-fast**；A 任务下单/查询落在 A account，不串 B。
- EMT/加密 account 不套 XTP 连接窗。

## 七、参考文档

1. `docs/architecture/A05-多账号源架构设计.md` §2.1/§三/§五契约8·9
2. `docs/architecture/模块契约/strategy_runner.md`（trading.py/main.py）+ `docs/architecture/接口契约.md` §（下单/查询/对账）
