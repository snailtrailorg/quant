# 模块契约 · strategies（策略实现示例）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（§策略层 StrategyConfig/Signal 等）。本文件不重复数据结构定义，只列"本模块暴露什么"。

## 职责
**策略实现层**：具体策略的业务逻辑（因子组合 + 选股 + 轮动 + 回测）。当前 1 个示例策略（可转债双低轮动）。
> ⚠️ 本模块与 `strategy_framework` 的边界：`strategy_framework` = **框架**（Strategy 基类/因子注册/回测引擎/适配器）；`strategies` = **具体策略实现**（用框架组装业务逻辑）。新策略应加文件到本目录，不改框架。

## 文件结构
```
server/src/strategies/
├── __init__.py                    # 空
└── convertible_doublelow.py       # 可转债双低轮动策略（示例）
```

---

## 一、public API（稳定，可跨模块调用）

### convertible_doublelow.py
```python
@dataclass
class DoubleLowConfig:
    top_n: int = 10                # 选前 N 只
    rebalance_days: int = 5        # 每 N 天轮动一次
    min_price: float = 80.0        # 最低价格过滤
    max_price: float = 150.0       # 最高价格过滤
    min_volume: float = 0          # 最低日均成交额(万元)，0=不限
    premium_weight: float = 1.0    # 溢价率权重（1.0=标准双低）

class ConvertibleDoubleLowStrategy:
    def __init__(self, config: DoubleLowConfig | None = None)
    def backtest(self, start_date: str, end_date: str) -> dict
        # start_date/end_date 格式 "YYYYMMDD"
        # 返回 {start_date, end_date, total_return_pct, max_drawdown_pct, total_days, final_value, top_n}
        # 无数据返回 {"error": "无可转债数据"}
    def _should_rebalance(self, d: date, idx: int) -> bool   # 内部：首次或距上次≥rebalance_days
```

---

## 二、内部 API（不保证稳定）

- `ConvertibleDoubleLowStrategy._last_rebalance: date | None`：上次轮动日（`_should_rebalance` 维护）
- `ConvertibleDoubleLowStrategy._holdings: list[str]`：当前持仓 vt_symbol 列表（声明但回测内未真正用）

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| convertible_doublelow.py | `strategy_framework`（Strategy/StrategyConfig/Signal/Action/SignalAggregator/create_adapter/list_factors/register_factor/BarContext） | 框架基类 + 因子（顶部 import） |
| convertible_doublelow.py | `data_platform.platform`（顶部 import） | 数据入口（声明用） |
| convertible_doublelow.py | `data_platform.adapters.tushare_adapter`（**函数内 lazy**：pull_cb_daily/to_save_rows） | 拉可转债日线 |
| convertible_doublelow.py | `data_platform.db`（**函数内 lazy**：save_bars/get_bars）/ `data_platform.to_vt_symbol` | 存读 K 线 + 代码转换 |

> ⚠️ 顶部 import 了 `strategy_framework` 的 Strategy/StrategyConfig/create_adapter/list_factors/register_factor/BarContext，但 `backtest` 方法**当前是自包含实现**（直接 pandas 模拟，未用 Strategy 基类/因子框架/BacktestEngine）。这些 import 是为后续重构到框架而预留，属 TODO。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| — | **当前无调用方**（独立示例策略，未被 web_api/scheduler 直接 import） |

> 本模块是策略实现的**样板/参考**。生产回测走 `strategy_framework.BacktestEngine` + Web 配置的 `strategy_config`（DSL 因子），不经本目录的策略类。新策略若走配置驱动 DSL，无需加文件到本目录。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `bar_1D` | —（回测内有 to_save_rows 调用但简化未真存） | `get_bars`（回测内 lazy import，当前简化未真正用） |

> 本模块当前回测直接用 `pull_cb_daily` 返回的 DataFrame 模拟，未实际读写 PG bar 表（代码预留）。无独占写表。

---

## 六、不变量

- **双低定义**：`double_low = close + premium_weight × premium_rate`（当前简化只用 close，premium_rate 待数据源接入后补，见 backtest 第 88 行注释）
- **日期格式**：`start_date`/`end_date` 为 `"YYYYMMDD"` 字符串（内部 strptime 转 date）
- **轮动周期**：首次必轮动；后续每 `rebalance_days` 天轮动一次（`_should_rebalance`）
- **初始资金**：硬编码 1_000_000（回测内）
- **价格过滤**：`min_price ≤ close ≤ max_price` AND `amount ≥ min_volume`（万元）
- **TODO**：未用 strategy_framework 的 Strategy 基类/因子/BacktestEngine（自包含 pandas 模拟），是过渡实现

---

## 七、扩展指南

### 加新策略实现（配置驱动，推荐）
1. **不改本目录**：走 `strategy_config` 表 + DSL 因子（`strategy_framework.Strategy.from_config`），Web 配置即生效
2. 因子不存在时：`strategy_framework.factor.py` 加 `@register_factor` + 可选 DSL

### 加新策略实现（代码类，参考本样板）
1. 本目录加 `<name>.py`，定义 `<Name>Config` dataclass + `<Name>Strategy` 类
2. 回测优先复用 `BacktestEngine.run(config, bars)`（不要自建 pandas 模拟）
3. 实盘走 `strategy_runner/main.py` + `strategy_config`（独立子进程 + XTPAdapter）

---

## 修订记录
- 2026-08-10 初版（基于代码核实：convertible_doublelow.py 全读 + 被调 grep 无命中）
