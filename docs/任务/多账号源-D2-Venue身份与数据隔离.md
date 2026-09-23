# 多账号源 D2 · Venue 身份与数据隔离（详细设计）

> 上级：`docs/architecture/31-多账号源架构设计.md` §二/§五契约1·6·7。状态：详细设计（待双盲审）。

## 一、目标

把「账户/持仓/资金/成交归因」从「策略级单账户」迁到「per-venue」，钉死 venue 身份锚点 + 数据隔离，防止混仓/成交归因错账户。

## 二、现状（已核实，file:line）

- `live_task.account_id: str`（迁移 `0025_live_task.py:32`）——策略级账户语义，非 venue。
- `position_snapshot`：已多行、有 `account_id TEXT` 主键 `(account_id,symbol,direction)`；存值 `str(account_id) if account_id else "default"`（`strategy_runner/trading.py:115`）。
- `account_snapshot`：**单行全局**（`id,ts,total_value,daily_pnl,initial_capital,available_cash`，无账户列）；读方 `ORDER BY ts DESC LIMIT 1`（`strategy.py:338`/`risk.py:169`/`chat.py:66`）；`initial_capital` 写侧已是「首条快照 total_value」基线（`trading.py:143 _account_baseline_capital`，#10 口径修正 2026-08-22，非策略级默认 100 万）。
- `strategy_account`：有 `leverage` + `initial_capital` 列；`strategy.py`/`main.py` 引用（`broker_provider` 是死列，仅被读出打日志未驱动选源）。
- `order_log`/`trade_log`：无 account/venue 列（trade_log 经 `order_id` 关联 order_log，两者均只有 strategy_id 无 venue）。
- `delete_interface` 守卫：需与 FK 同版本（防静默级联删实盘任务）。

## 三、设计决策（已裁定 2026-09-23）

### D2-1 稳定语义键取值（XTP 域）
- **✅ `external_interface` 新增 `account_key` 列，`UNIQUE(provider, account_key)`，取值 = XTP 域「资金账号」**（加密 venue 无资金账号，取 API uid / 用户自定稳定标签；复合唯一防跨券商资金账号撞号——两券商可能分配相同数字资金账号）。
- 理由：资金账号每券商账号唯一且稳定，契合「删行重建不换语义键」；「股东账号」沪/深各一（一对二），不适合作单列语义键——股东账号放 `credentials_encrypted`/`params` 供实际下单用，不进语义键。

### D2-2 leverage 去向：venue 级（账号级）
- **✅ `strategy_account` 退役时，`leverage` 迁到 venue 级配置**（账号级杠杆）；`initial_capital` 走 per-venue 资金基线（见 D2-3）。
- 理由：leverage 是「账户级敞口」实参，账户级敞口按框架 per-venue；杠杆约束本质来自券商/交易所（账号级）。策略要更低杠杆是另一回事——per-任务下注参数，别和「账户杠杆」混成一列。

### D2-3 资金基线写侧来源
- **✅ 资金基线 per-venue = 该 venue 首条快照 `total_value`（跟踪起点净值）**，非用户名义入金数；显式配置（建任务/入金）仅作参考展示，**不作回撤分母**。
- 理由：只钉读方不钉写方=每个 venue 读到错基线，等价混仓。基线=首条快照是既有 #10 口径（`trading.py:143 _account_baseline_capital` 2026-08-22 已修）；回退到「用户名义入金数」会重演「配置 100 万 vs 真实 10 亿 → total_pnl 虚增 9.99 亿 → 回撤熔断恒不触发（fail-open）」。

## 四、契约

- `external_interface.account_key TEXT`，`UNIQUE(provider, account_key)`（语义键）；`live_task.venue_id BIGINT NOT NULL REFERENCES external_interface(id) ON DELETE RESTRICT`。
- `position_snapshot.account_id` → `venue_id BIGINT FK`（改名+类型转换+回填）。**回填规则**：`"default"` 映射到唯一默认 venue；其余存量 `distinct account_id` 逐一映射到 venue 行，**无匹配行=显式建 venue 行或 fail-explicit（禁静默归当前 venue）**；改键后 `(venue_id,symbol,direction)` 若有碰撞（两 account_id 映射同一 venue_id），迁移前置去重并定胜负，禁静默丢行。
- `account_snapshot` 新增 `venue_id` 列 + 多行化。**读方全清单**（补全，防混仓）：`strategy.py:317 _held_volume`（SELL 截断，仅 WHERE symbol）、`strategy.py:327 _held_value`（PERCENT/ALL_IN sizing）、`risk.py:166`（敞口，仅 WHERE symbol）、`web_api/routes/trading.py:218/228`（首条快照基线读方 + /api/position）、`scheduler/tasks.py:211`（持仓账实分离 diff，JOIN trade_log 无 venue 维）——全部改按 venue 过滤。**写侧**：`risk.py:439 update_account_snapshot`、`trading.py snapshot_cycle` 多行化后必须带 venue_id（来源=per-venue adapter.query_account 返回值，依赖 D5）。
- `order_log`/`trade_log` 新增 `venue_id` 列 + 回填。**回填规则**：历史行 `venue_id` 置 NULL（NULL=未知 venue，禁「全归当前 venue」静默回填）；对账 per-venue 归因从 cutover 日起启用（cutover 前归因按旧 strategy_id 语义）。
- `live_task.venue_id` 加列后存量行回填：`live_task.account_id` 是自由文本，未必映射到任何 venue——**无法映射的置错误态人工处置，禁乱填**；可映射的映射到对应 venue。
- 身份线统一：`live_task.account_id`/`position_refresh.account_id` → `venue_id`；`strategy_account` 退役。
- `delete_interface` 守卫与 FK 同版本（RESTRICT 防删有实盘任务的 venue）。

## 五、限定范围

只做：`external_interface.account_key` 列 + 迁移、`live_task.venue_id` 列、快照/订单日志 venue_id 列 + 回填、身份线统一、`strategy_account` 退役 + leverage/initial_capital 迁移、`delete_interface` 守卫。
不碰：D1（权限/venue_permission）、D3（流协议）、D4（费率对账）、D5（TD 网关）、D6（hub 部署）。

## 六、验收标准（编码阶段定，行为级）

- 迁移 upgrade/downgrade 双向绿（account_key + venue_id 列 + 回填 + 退役 strategy_account）；**回填规则：历史 order/trade_log venue_id=NULL、position_snapshot 改键去重不丢行、live_task 无法映射的置错误态**。
- 多 venue 下：下单量/敞口/资金基线读方 per-venue 隔离（A venue 成交不归因到 B venue，含 `_held_volume`/`_held_value`/账实分离 diff）。
- `delete_interface` 删有实盘任务的 venue 被 RESTRICT 拒绝。
- `pytest tests/ -q` 全绿 + 分层绿 + pyflakes 零新增。

## 七、参考文档

1. `docs/architecture/31-多账号源架构设计.md` §二/§五契约1·6·7
2. `docs/architecture/模块契约/strategy_runner.md`（trading.py 九单元）+ `docs/architecture/接口契约.md` §（快照/对账）
