# 模块契约 · astock_analysis（A股分析引擎）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（AnalysisResult 见本文 §一；Bar/BarContext 见 §K 线层）。本文件不重复数据结构定义。

## 职责
A股分析引擎：**日线选股**（多因子打分 + 排序）+ **分钟级研判**（盘中实时，占位待 D2 实现）。
输出 `AnalysisResult` 供 Web 看板展示。**A 股只读不下单**--实盘交易走 XTPAdapter（受三级开关控制，非本模块职责）。

## 文件结构
```
server/src/astock_analysis/
├── analysis.py     # AnalysisResult + DailySelectionEngine + MinuteAnalysisEngine
└── __init__.py     # 暴露 DailySelectionEngine（from .analysis import DailySelectionEngine）
```

---

## 一、public API（稳定，可跨模块调用）

### AnalysisResult（dataclass，analysis.py）
```python
@dataclass
class AnalysisResult:
    ts: str                      # 交易日（YYYYMMDD）
    symbol: str                  # ts_code（600000.SH）
    vt_symbol: str               # 600000.SHSE
    model: str = "daily_select_v1"
    score: float = 0.0           # 综合评分（ma_dev*2 + momentum*1.5 + vol_ratio*0.5）
    rating: str = "HOLD"         # BUY / HOLD / AVOID（阈值 ±0.3）
    factors: dict = {}           # {"ma_dev":..., "momentum":..., "vol_ratio":...}
    support: float = 0.0         # 近 20 日最低
    resistance: float = 0.0      # 近 20 日最高
    conclusion: str = ""         # 自然语言结论（因子值汇总）
    llm_summary: str = ""        # LLM 增强研判（enhance_with_llm 填）
```

### DailySelectionEngine（日线选股，2026-08-20 横截面重写：U 审项 5）
```python
DailySelectionEngine(top_n: int = 30, max_stocks: int | None = None)
    # max_stocks 默认 5000（防呆上限）——横截面批量全市场，不再逐标的打 API

.run(trade_date: str | None = None) -> list[AnalysisResult]
    # 一次 SQL 全市场横截面（daily_basic 45 日窗口 + LEFT JOIN moneyflow/cyq_perf
    # + asset_static_info ST/退过滤），Pandas 因子 rank(pct) 归一 [-1,1] 加权打分
    # → 降序 top_n；rating=分位（BUY≥q85 / AVOID≤q15 / HOLD）
    # 数据源全部本地零 API（原实现逐标的 pro.daily 50 只上限）；trade_date 参数
    # 仅透传到结果 ts（横截面永远取 MAX(trade_date) 最新日——历史日查询不再支持）
    # 缺数因子列（notna<30，如 cyq_perf 未同步）整体跳过（中性），权重按活跃因子重分配

SELECTION_FACTORS: dict[str, dict]   # 横截面因子注册表（配置驱动：加因子=加条目）
    # net_mf_pct 主力净流入/流通市值 ×2 / lg_flow_pct 大单净额 ×1
    # winner_rate 获利盘(cyq_perf) ×1.5 / ma_dev 20日均线偏离 ×1.5
    # 每项 {weight, direction(1/-1), col(SQL 列), desc}

.enhance_with_llm(results) -> list[AnalysisResult]
    # 前 5 只 LLM 研判（caller="astock"），不可用时 llm_summary 占位不抛（未改）
```

### MinuteAnalysisEngine（分钟级研判，**D2 待实现**）
```python
MinuteAnalysisEngine()
.on_bar(bar: dict) -> dict
    # ⚠️ 当前占位：返回 {"action": "HOLD", "signal": "暂未实现"}
    # D2 目标：bar（1min/5min，{ts,open,high,low,close,volume}）-> 因子 -> 信号 -> 研判 dict
```

---

## 二、内部 API（不保证稳定，改模块时才能动）

- `DailySelectionEngine._XSECTION_SQL`：横截面 SQL（CTE latest/ma + 三 LEFT/JOIN；date 型 daily_basic 与 text 型 moneyflow/cyq_perf 的 trade_date 键以 to_char 桥接）
- `SELECTION_FACTORS` 常量：见 public API 节（注册表即配置）
  - 支撑/阻力：近 20 日 min/max

---

## 三、依赖（import 其他模块什么）

| 依赖 | 用途 |
|---|---|
| `src.strategy_framework`（Strategy/StrategyConfig/Signal/Action/BarContext/SignalAggregator/create_adapter/list_factors/register_factor/get_factor） | 因子注册 + BarContext + 信号 |
| `src.data_platform`（platform/to_vt_symbol/parse_vt_symbol） | vt_symbol 转换 + 数据访问 |
| `src.data_platform.adapters.tushare_adapter.get_pro` | `run()` 内拉日线（注意：当前直连 tushare，不经 DataSource DB） |
| `src.llm_gateway.gateway` | `enhance_with_llm`（caller="astock"） |
| `pandas` / `numpy`（外部） | DataFrame + 均值/极值 |

> ⚠️ `run()` 当前用 `get_pro()` 直连 tushare（不经 `get_data_source` DB）。PT3 平台化后应改走 `platform.get_bar` 读 PG（已同步的 bar_1D），减少 tushare 直调。D2 实现时注意数据来源选择。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `web_api.main` `/api/astock/selection` | `DailySelectionEngine(top_n=20).run(trade_date)` -> 返回 list[dict] |
| `web_api.main` `/api/screen/astock` | 筛选端点（可能复用 AnalysisResult，待核实） |
| `scheduler.tasks` 盘后选股 | 定时跑 `DailySelectionEngine.run()`（待接入） |
| `strategy_framework` 因子注册 | `get_factor("ma_dev"/"rsi"/"volume_ratio")` 被本模块调用 |

> D2 实现后，`MinuteAnalysisEngine` 的调用方待定（可能是 scheduler 盘中驱动 / web_api SSE 推送 / 策略实盘 on_bar）。改 `on_bar` 签名影响这些。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `bar_1D` | - | `platform.get_bar` / `db.get_bars`（D2 后改走 PG） |
| `bar_1min` / `bar_5min` | - | D2 分钟研判读（migration 0022 已建） |
| `trade_cal` | - | `is_trading_day`（判断交易日） |
| `daily_basic` | - | PE/PB/市值筛选（待核实是否本模块用） |

> ⚠️ 当前 `DailySelectionEngine.run` **不写 PG**--直接返回 list[AnalysisResult]，Web 端点转 dict 返回。注释提"输出存 PG 供看板"是 TODO（盘后选股结果持久化，待 scheduler 接入时加）。D2 不涉及持久化，只算研判。

---

## 六、不变量

- **A 股只读**：本模块永远不下单（adapter 不 send_order）；实盘走 XTPAdapter + 三级开关
- **rating 阈值**：`score > 0.3` = BUY；`score < -0.3` = AVOID；中间 = HOLD
- **score 公式**：`ma_dev*2 + momentum*1.5 + vol_ratio*0.5`（权重硬编码，未来走配置）
- **因子复用**：不自建因子，复用 `strategy_framework` 注册的 `ma_dev`/`rsi`/`volume_ratio`
- **vt_symbol**：`to_vt_symbol(ts_code)` 转换（`600000.SH` -> `600000.SHSE`）
- **LLM 增强**：`enhance_with_llm` 只处理前 5 只（限流），失败不抛（填占位文本）
- **分钟数据来源**：D2 实现时优先读 PG `bar_1min`（A1 已建），实时行情订阅是 #4 实盘化范畴（不在 D2）

---

## 七、扩展指南

### D2 实现 MinuteAnalysisEngine.on_bar（当前占位）
1. 输入 `bar: dict`（{ts, open, high, low, close, volume}，1min/5min）
2. 构建 `BarContext`（已有，防未来函数）或直接用 factor
3. 算分钟级因子（如短期动量/量比突变/突破）
4. 输出研判 dict（{action, score, rating, signal, ...}），可能复用 AnalysisResult 或新建 MinuteResult
5. **不改** DailySelectionEngine（日线选股独立）
6. **不接实时订阅**（那是 #4 实盘化 / 行情驱动），on_bar 只做"收到 bar -> 研判"

### 加新 A 股因子
1. `strategy_framework/factor.py` 注册（`@register_factor`）
2. `_register_astock_factors` 加 `get_factor(name)` 一行

### 选股结果持久化（未来）
1. migration 建 `astock_selection` 表（ts/symbol/score/rating/factors JSON）
2. `run()` 末尾写表
3. Web 端点改读表（缓存历史）

---

## 修订记录
- 2026-08-10 初版（基于代码核实：analysis.py 185 行全读 + __init__.py）
