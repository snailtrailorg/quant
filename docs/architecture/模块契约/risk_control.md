# 模块契约 · risk_control（风控中心）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（跨模块签名 + 数据结构）。本文件不重复数据结构定义，只列"本模块暴露什么"。

## 职责
全局总风控 + 分市场独立风控双层。**所有自动交易下单前必过 `check_order`**；一键熔断状态存 Valkey（禁止内存缓存）；实盘三级开关第二级（`.env` 总闸 AND Web 分项）在此前置检查。
独立提供 `RiskRule` 单规则抽象（PT6，别人实现接口加规则，不改 `risk_control`）。

## 文件结构
```
server/src/risk_control/
├── risk.py         # RiskControl 单例 + RiskDecision/RiskState + 三级开关 + 熔断 + 分市场检查
├── risk_rule.py    # RiskRule 接口（PT6）+ MaxPosition/MaxSingleOrder/DailyLossLimit + 注册表
└── __init__.py     # 导出 RiskControl/RiskDecision/RiskState
```

---

## 一、public API（稳定，可跨模块调用）

### risk.py（单例 `RiskControl.get()`）
```python
RiskControl.get() -> RiskControl                  # 单例（__init__ 连 Valkey + 读 risk_rules/DEFAULT_RULES）
.check_order(order: dict, account: str = "") -> RiskDecision
    # 前置四步：1.熔断 2.三级开关(market=_market_of(symbol)) 3.全局回撤/亏损 4.分市场(_check_etf_conv/_check_crypto)
    # 拒单返回 RiskDecision(approved=False, reason, severity)；不抛
.is_halted() -> bool                              # ⚠️ 永远直读 Valkey，禁内存缓存
.emergency_halt(reason: str = "manual") -> None   # 一键熔断（Admin/飞书/Web 按钮）
.resume() -> None                                  # 解除熔断（仅 Admin）
.halt_reason() -> str | None
.is_live_trading_allowed(market: str) -> bool      # 三级第二级：.env 总闸 AND live_trading_config 分项
.update_account_snapshot(total_value, daily_pnl=0, initial_capital=1_000_000)  # 策略/交易引擎调，供风控读
.get_rules() -> dict                              # 返回 global/etf_conv/crypto 三段 dict（副本）
.update_rules(rules: dict) -> None                # 内存更新（Admin）；DB 持久化走 web_api risk_rules 端点
```

### risk.py 数据结构（dataclass，见接口契约 §RiskDecision/RiskState）
```python
RiskDecision: approved: bool / reason: str / severity: Level="info" / adjusted: dict | None = None
    # adjusted = B8 风控覆写（如超仓位截断 volume 后的修正 order；None=不覆写）
RiskState: halted: bool / total_drawdown: float / daily_loss: float
Level = Literal["info", "warn", "critical"]
```

### risk_rule.py（PT6 单规则抽象，详见接口契约 §5 RiskRule）
```python
RiskCheckResult: approved: bool / reason: str = ""
RiskRule(ABC): .check(order, context) -> RiskCheckResult / .get_params() -> dict
get_rule(rule_type: str, params: dict | None = None) -> RiskRule | None
load_rules_from_db() -> list[RiskRule]            # 读 risk_rules 表 enabled=true（type=max_position 等）
_REGISTRY: dict[str, type[RiskRule]]              # max_position / max_single_order / daily_loss_limit
```

> ⚠️ **RiskRule（单规则）与 RiskControl（dict 参数）独立**：`RiskControl` 自己读 `risk_rules` 表 `type=global/etf_conv/crypto` 行的 params（`_load_rules_from_db`），**不用** `load_rules_from_db`；`RiskRule` 接口保留给新单规则扩展。详见接口契约 §5。

---

## 二、内部 API（不保证稳定，改模块时才能动）

- `RiskControl._market_of(symbol) -> str | None`：vt_symbol → convertible/etf/astock/binance_perp/okx_perp/None（兼容 SHSE/SZSE/SSE 后缀；11/12 可转债、51/15/56 ETF、60/00/30 A 股）
- `RiskControl._load_rules_from_db()`（静态）：读 risk_rules 表，无配置 fallback `DEFAULT_RULES`
- `RiskControl._check_etf_conv(order) / _check_crypto(order)`：分市场检查（#29 单笔金额超限截断 volume 返回 adjusted）
- `RiskControl._get_global_state(account) -> RiskState`：读 account_snapshot 最新行算回撤/亏损；无数据返回 0.0；异常返回 0.0（不抛）
- `RiskControl._HALT_KEY="risk:halted"` / `_HALT_REASON_KEY="risk:halt_reason"`
- `DEFAULT_RULES`：global（max_drawdown=0.15/daily_loss_limit=0.05）/ etf_conv（single_position_pct/max_trades_per_day/strict_stop_loss/max_single_amount）/ crypto（leverage_max=5/margin_mode=isolated/pin_protection/daily_loss_limit）

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| risk.py | `data_platform.db.get_conn` | 读 risk_rules / live_trading_config / account_snapshot |
| risk.py | `data_platform.settings.is_live_trading_enabled` | 三级第一级总闸（函数内 import） |
| risk.py | redis（外部）/ dotenv | Valkey 熔断状态 / .env |
| risk_rule.py | `data_platform.db.get_conn`（load_rules_from_db） | 读 risk_rules |

> 无循环依赖（risk_control 不被 data_platform 反向依赖）。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `strategy_framework.strategy.Strategy.place_order` | `RiskControl.get().check_order`（下单前置；用 `decision.adjusted` 覆写 order） |
| `web_api.main` | risk 端点：`is_halted`/`halt_reason`/`get_rules`（state）、`emergency_halt`（halt）、`resume`（resume）；risk_rule CRUD 用 `risk_rule._REGISTRY` |
| `scheduler.tasks.risk_sweep` | `is_halted` / `halt_reason` |
| `scheduler.tasks.daily_report` | `is_halted`（盘后报告内容） |
| `feishu_bot.bot` | `emergency_halt` / `resume`（LLM 工具调用）/ `is_halted` |
| `llm_gateway.gateway` | 注册 `emergency_halt` 为 Tool（ trader/admin 可调） |

> 改 `check_order` / `_market_of` 签名影响**策略下单链 + 飞书/Web 熔断**——慎改。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `risk_rules` | web_api（CRUD 端点） | `RiskControl._load_rules_from_db`（type=global/etf_conv/crypto）/ `risk_rule.load_rules_from_db`（type=max_position 等） |
| `live_trading_config` | web_api（实盘开关端点） | `RiskControl.is_live_trading_allowed` |
| `account_snapshot` | `RiskControl.update_account_snapshot`（INSERT；`_get_global_state` 内 `CREATE TABLE IF NOT EXISTS` 兜底） | `RiskControl._get_global_state` |

> account_snapshot DDL 在 `_get_global_state`/`update_account_snapshot` 内幂等建；正式 schema 走 alembic migration。

---

## 六、不变量

- **熔断永远直读 Valkey**：`is_halted()` 每次查 `risk:halted`，禁 `self._halted` 内存缓存（多进程一致性）
- **三级开关 AND**：`.env ENABLE_LIVE_TRADING`（`settings.is_live_trading_enabled()`）+ Web `live_trading_config` 分项（`is_live_trading_allowed(market)`，在 `check_order` 前置）+ 策略 `enabled`+`backtest_verified`（策略层/scheduler 检查）。任一关 → 拒单
- **check_order 不抛**：熔断/未授权/超限都返回 `RiskDecision(approved=False, reason, severity)`；account_snapshot 读失败返回 0.0（无风险放行）
- **风控覆写（B8/#29）**：`RiskDecision.adjusted` 非 None 时调用方用 adjusted（如超 max_single_amount 截断 volume）；`strategy.place_order` 已支持
- **market 判定**：`_market_of` 未知品种返回 None（拒单"未授权实盘品种"）；可转债 11/12、ETF 51/15/56、A 股走 astock 分项
- **RiskRule vs RiskControl 独立**：见上文 ⚠️；`risk_control` 不调 `load_rules_from_db`

---

## 七、扩展指南

### 加新风控规则（如"日内次数限制"）
1. 实现 `RiskRule` 子类（`check(order, context)` + `get_params()`）
2. `risk_rule._REGISTRY["max_trades_per_day"] = MaxTradesPerDayRule`
3. Web 配 `risk_rules`（type=max_trades_per_day，params JSON，enabled）
4. 不改 `risk_control`（`RiskRule` 接口保留新规则；后续 `check_order` 可改为遍历 `load_rules_from_db()` 调各 `RiskRule.check`）

### 加新分市场（如港股）
1. `DEFAULT_RULES` 加 `"hk": {...}`
2. `_market_of` 加 `.HKEX` 后缀判定
3. `check_order` 第 4 步加分发分支 `_check_hk`
4. `live_trading_config` 加 `hk` 分项（web_api LIVE_TRADING_MARKETS）

### 改全局阈值
- 临时：`rc.update_rules({...})`（内存，重启失）
- 持久：Web 改 risk_rules 表（type=global 的 params JSON）

---

## 修订记录
- 2026-08-11 初版（基于代码核实：risk.py:1-264 / risk_rule.py:1-106 / __init__.py 全读）
