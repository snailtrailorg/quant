# 模块契约 · astock_analysis（A股分析引擎）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（AnalysisResult 见本文 §一；Bar/BarContext 见 §K 线层）。本文件不重复数据结构定义。

## 职责
A股分析引擎：**日线选股**（横截面多因子分位打分 + 排序）+ **分钟级研判**（D2 已实现 2026-08-10）+ 可转债条款解读（convertible_terms.py）。
输出 `AnalysisResult` 供 Web 看板展示。**A 股只读不下单**--实盘交易走 XTPAdapter（受三级开关控制，非本模块职责）。

## 文件结构
```
server/src/astock_analysis/
├── analysis.py           # AnalysisResult + DailySelectionEngine + MinuteAnalysisEngine + SELECTION_FACTORS
├── convertible_terms.py  # D3 可转债条款解读（pull_cb_basic + LLM 摘要）
└── __init__.py           # 暴露 DailySelectionEngine（from .analysis import DailySelectionEngine）
```
（P3 回写 2026-08-20：文件结构补 convertible_terms.py；职责句删"占位待 D2"）

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
    score: float = 0.0           # 综合评分（DailySelectionEngine=横截面 rank(pct) 归一 [-1,1] 加权；分位制见 SELECTION_FACTORS）
    rating: str = "HOLD"         # BUY / HOLD / AVOID（日线=分位：BUY≥q85 / AVOID≤q15；分钟引擎仍用 ±0.3 阈值）
    factors: dict = {}           # 日线={"net_mf_pct":..., "winner_rate":..., "ma_dev":...}（SELECTION_FACTORS 键）；分钟={"ma_dev","momentum","vol_ratio"}（内联计算）
    # （P3 回写 2026-08-20：原"ma_dev*2+momentum*1.5+vol_ratio*0.5 / 阈值±0.3"为 2026-08-20 横截面重写前的旧公式，现仅存于 MinuteAnalysisEngine）
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

### MinuteAnalysisEngine（分钟级研判，**D2 已实现 2026-08-10**；P3 回写 2026-08-20）
```python
MinuteAnalysisEngine()
.on_bar(bar: dict, history: list[dict] | None = None) -> dict
    # bar: {ts, open, high, low, close, volume}（1min/5min）
    # history: 过去 bar 列表（构建因子上下文防未来函数；None 时只用当前 bar）
    # 内联算 ma_dev/momentum/vol_ratio（旧公式 ma_dev*2+momentum*1.5+vol_ratio*0.5 + ±0.3 阈值仅存于此）
    # 返回 {"action", "score", "rating", "conclusion", "factors"}
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
| `src.data_platform`（db.get_conn/to_vt_symbol/parse_vt_symbol） | vt_symbol 转换 + 横截面 SQL 直读 PG |
| `src.data_platform.adapters.tushare_adapter.pull_cb_basic` | 可转债条款（convertible_terms.py；非选股链） |
| `src.llm_gateway.gateway` | `enhance_with_llm`（caller="astock"）+ 条款解读 |
| `pandas` / `numpy`（外部） | DataFrame + rank/分位 |

> （P3 回写 2026-08-20）`DailySelectionEngine.run` **已改为本地横截面零 API**（一次 SQL 读 daily_basic/moneyflow/cyq_perf/asset_static_info，Pandas rank(pct) 打分）——原"get_pro() 直连 tushare 逐标的拉日线"为 2026-08-20 重写前旧实现，相关 PT3 改造注记已随重写消解。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `web_api.main` `/api/astock/selection` | `DailySelectionEngine(top_n=20).run(trade_date)` -> 返回 list[dict] |
| `web_api.main` `/api/screen/astock` | 筛选端点（可能复用 AnalysisResult，待核实） |
| `scheduler.tasks.astock_select_daily` | 盘后选股已接入（tasks.py:47 起，非交易日跳，`DailySelectionEngine(top_n=20).run(今日)`）（P3 回写 2026-08-20：原"待接入"过时） |
| `web_api.main` `/api/convertible/terms` | `convertible_terms.analyze_convertible_terms`（D3 条款解读） |

> （P3 回写 2026-08-20：删"strategy_framework get_factor 被本模块调用"行——横截面重写与 D2 内联实现后不再 import strategy_framework；删"D2 调用方待定"句——D2 已实现，实时驱动方接入时再补）

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `daily_basic` | - | `DailySelectionEngine._XSECTION_SQL` 横截面主表（45 日窗口 + ma）+ web_api 筛选 |
| `moneyflow` | - | 横截面 LEFT JOIN（net_mf_pct/lg_flow_pct 因子） |
| `cyq_perf` | - | 横截面 LEFT JOIN（winner_rate 因子；缺数整体跳过中性） |
| `asset_static_info` | - | 横截面 ST/退过滤 |
| `bar_1min` / `bar_5min` | - | 分钟研判数据源（D2 已实现） |
| `trade_cal` | - | `is_trading_day`（判断交易日） |

> （P3 回写 2026-08-20：补横截面重写后实际读的 4 表，删原 bar_1D/daily_basic"待核实"旧行）`DailySelectionEngine.run` **不写 PG**——直接返回 list[AnalysisResult]（盘后选股结果持久化仍为 TODO）。

---

## 六、不变量

- **A 股只读**：本模块永远不下单（adapter 不 send_order）；实盘走 XTPAdapter + 三级开关
- **rating 分位制（P3 回写 2026-08-20）**：日线=横截面分位——BUY≥q85 / AVOID≤q15 / 中间 HOLD（`analysis.py:137`）；旧"`>0.3`/`<-0.3` 阈值 + `ma_dev*2+momentum*1.5+vol_ratio*0.5` 公式"仅存于 MinuteAnalysisEngine（分钟引擎）
- **score 权重配置驱动**：`SELECTION_FACTORS` 注册表（每项 {weight, direction, col, desc}），加因子=加条目；缺数因子列（notna<30）整体跳过、权重按活跃因子重分配
- **横截面零 API**：`run()` 一次 SQL 全市场（daily_basic 45 日窗口 JOIN moneyflow/cyq_perf/asset_static_info），不逐标的打 tushare（P3 回写 2026-08-20）
- **vt_symbol**：`to_vt_symbol(ts_code)` 转换（`600000.SH` -> `600000.SHSE`）
- **LLM 增强**：`enhance_with_llm` 只处理前 5 只（限流），失败不抛（填占位文本）
- **分钟数据来源**：读 PG `bar_1min`/`bar_5min`（D2 已实现）；实时行情订阅是 #4 实盘化范畴

---

## 七、扩展指南

### MinuteAnalysisEngine.on_bar（D2 已实现；P3 回写 2026-08-20）
- 已实现（analysis.py:194 `on_bar(bar, history=None)`）：内联算 ma_dev/momentum/vol_ratio → score → action/rating/conclusion dict
- **不接实时订阅**（那是 #4 实盘化 / 行情驱动），on_bar 只做"收到 bar -> 研判"

### 加新 A 股选股因子（P3 回写 2026-08-20：横截面重写后走注册表）
1. `SELECTION_FACTORS` 加条目：`{weight, direction(1/-1), col(SQL 列), desc}`
2. 确认该列已在 `_XSECTION_SQL` 的 SELECT/JOIN 里；不加不改引擎逻辑

### 选股结果持久化（未来）
1. migration 建 `astock_selection` 表（ts/symbol/score/rating/factors JSON）
2. `run()` 末尾写表
3. Web 端点改读表（缓存历史）

---

## 修订记录
- 2026-08-10 初版（基于代码核实：analysis.py 185 行全读 + __init__.py）
