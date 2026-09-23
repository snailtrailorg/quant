# 多账号源 D5 · 建任务与 worker 绑定（详细设计）

> 上级：`docs/architecture/31-多账号源架构设计.md` §二·2.1/§三/§五契约8·9。状态：详细设计（待双盲审）。依赖：D1（venue_allows）、D2（venue_id）、D3（流键）。

## 一、目标

钉死「下单走哪条柜台链路」：建任务显式选源、三级时点权限校验、TD 网关按 venue 构建（禁硬编码）、XTP 连接窗分支。

## 二、现状（已核实）

- `strategy_runner/main.py:199` 硬编码 `XTPAdapter(gateway=gw, ...)`——TD 网关不走 venue 配置。
- `main.py:384` 从 `strategy_account.broker_provider`（自由文本）选源——身份线未收编到 venue。
- `perm_registry.py:56` market_op 五键下单卡口（role 级，非 venue 级）。
- XTP 每日连接窗（`system_config` `xtp_session_lead_min`/`lag_min`）只对 A股 XTP 生效；EMT/加密不套（31 号 §五契约9）。

## 三、设计决策

- **无首决**。选源/校验/TD 网关方向已在 31 号定，本件落成契约 + 接入位。

## 四、契约

- **选源**：TD 人工显式选源（建任务 Web 选 venue_id）；候选收窄到 **enabled + capabilities ∋ trading**（加密 venue 无 quote，不得 trading+quote 收窄）。
- **三级时点**：①建任务拒绝（`venue_allows`）②worker 启动 fail-fast ③下单前拦截（`risk_control.check_order` 前置）。
- **TD 网关 per-venue 构建**：按 venue 的 provider+凭证构建网关/适配器，**禁硬编码 XTPAdapter/全局单例**；`query_position`/`query_account`/`order`/对账全走该 venue 会话。
- **XTP 连接窗分支**：只 A股 XTP 套每日连接窗（lead/lag）；EMT/加密不套（常连）。

## 五、限定范围

只做：建任务选源 + 三级时点接入 + TD 网关 per-venue 构建 + XTP 连接窗分支。
不碰：D1（venue_allows 实现，只调用）、D2（venue_id schema，只消费）、D3（流键，只消费）、D4（金融面）、D6（hub 部署）。

## 六、验收标准（编码阶段定，行为级）

- 建任务候选不含无 trading 能力的 venue。
- 建任务选无权限品种的 venue 被拒；worker 启动 fail-fast；下单前拦截。
- TD 网关按 venue.provider 构建（EMQ/EMT venue 不再走硬编码 XTPAdapter）；A 任务下单/查询落在 A venue，不串 B。
- EMT/加密 venue 不套 XTP 连接窗。

## 七、参考文档

1. `docs/architecture/31-多账号源架构设计.md` §2.1/§三/§五契约8·9
2. `docs/architecture/模块契约/strategy_runner.md`（trading.py/main.py）+ `docs/architecture/接口契约.md` §（下单/查询/对账）
