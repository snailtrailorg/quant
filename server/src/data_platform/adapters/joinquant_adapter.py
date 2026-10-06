"""聚宽 adapter（批 103b 真接）——`jqdatasdk` A 股历史切片。

**为什么独立成文件（原为 `base.py` 内 stub）**：真接后本模块持有 jqdatasdk 的形状知识
（`fq`/`panel`/`skip_paused` 三参数语义、单/多标的返回形状分叉、`factor` 随 `fq` 变），
与 `binance_adapter.py` 同列——`base.py` 只留 `TushareAdapter`（其代码即在此文件内）。

**真源事实（2026-10-06 dev 实测，勿凭文档改写）**：

1. **auth 门**：jqdatasdk 一切调用（含纯本地的 `normalize_code`）前置 `auth` ⇒ 走
   `JoinQuantDataSource.get_client()`（返回已 auth 的模块句柄）。
2. **`fq=None` 必须显式传**：默认 `fq='pre'`（前复权）会改价，破坏「未复权价」契约。
3. **`panel=False` 必须显式传**：`panel=True` 是已废弃的 Panel 返回。
4. **返回形状按入参分叉**（⚠️ 实现陷阱）：
   - 单标的 → **DatetimeIndex** + 字段列，**无 `code`/`time` 列**；
   - 多标的 → **RangeIndex** + `time`/`code` 列。
   `_normalize_frame` 统一两路。
5. **单位无需换算**：聚宽 `volume`=股、`money`=元，与内部 `bar_1d.volume/amount` **同单位**
   （实测同键对照：95,459,569 / 1,042,306,456 两侧完全一致）。
6. **复权因子不可跨源互换**（见任务文件 §0.4）：`factor` 随 `fq` 变——`fq=None` 恒 1.0（占位）、
   `fq='post'` 才是真后复权因子（146.360309），而 tushare 同键为 134.5794（**基准不同**）。
   ⇒ 本 adapter 的 `to_bar_rows` **恒置 `adj_factor=None`**，由落库侧的
   `COALESCE(EXCLUDED.adj_factor, bar_1d.adj_factor)` 保护 tushare 已回填值。
7. **窗口绝对区间 + SDK 边界检查不对称**：只查 `end_date`、**不查 `start_date`**
   ⇒ 窗口夹取是**调用方（engine handler）的责任**，本 adapter 只做单次调用。
8. **不覆盖北交所**（实测仅 XSHE+XSHG=5537；`static_symbols` 含 `.BJ`）⇒ **发请求前剔除**
   非 XSHE/XSHG 标的（`_JQ_EXCHANGES`）。且 jqdatasdk 对含无效标的的批次**整批拒绝**
   ⇒ 单批失败必须**二分降级**隔离坏标的，否则一只坏标的=800 只静默丢失（2026-10-06 实测）。
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from src.data_platform.adapters.base import BaseDataAdapter, UnsupportedFeature, register_adapter
from src.quant_common.contract import ASTOCK_ALL, CapabilityDecl

logger = logging.getLogger("data_platform")

# 聚宽交易所后缀 → 内部 tushare ts_code 后缀 / 反向
_JQ_TO_TS = {"XSHE": "SZ", "XSHG": "SH"}
_TS_TO_JQ = {"SZ": "XSHE", "SH": "XSHG", "SZSE": "XSHE", "SHSE": "XSHG"}

# 聚宽**仅**覆盖深/沪（实测 3077 XSHE + 2460 XSHG；**无北交所**）⇒ 其余交易所
# （`920000.BJ` 等）必须在**发请求前**剔除，否则 jqdatasdk 对整批只回一句
# `无效的证券代码 '920000.BJ'` ⇒ **整批 800 只全丢**（2026-10-06 dev 实测：一批 772 只
# 因一只北交所标的而全灭，日志仅一行 warning＝静默数据洞）。
_JQ_EXCHANGES = frozenset({"XSHE", "XSHG"})

# 单次 get_price 的标的上限（多标的形状；过大单请求易超时——按此切批）
_SYM_BATCH = 800

# 取数字段（一次拿全 OLHCV+money，免二次调用）
_FIELDS = ["open", "close", "high", "low", "volume", "money"]

# 批 107：聚宽「未退市」哨兵日（`get_all_securities.end_date` 实测）——归一为内部空串
_JQ_LISTED_SENTINEL = "22000101"


def _board_of_code(ts_code: str) -> str:
    """ts_code → `asset_static_info.market` 中文板块（词表同 tushare `stock_basic.market`）。

    聚宽 `get_all_securities` **无此字段** ⇒ 由代码前缀**确定性推导**（避免 `security_master.board`
    全空）。词表＝`security_master.normalize_board` 的输入：主板/创业板/科创板。
    """
    code = str(ts_code).split(".")[0]
    if code[:3] in ("300", "301"):
        return "创业板"
    if code[:3] in ("688", "689"):
        return "科创板"
    return "主板"


def _delist_or_blank(v) -> str:
    """聚宽 `end_date` → 内部 `delist_date`：哨兵 `2200-01-01`（未退市）→ 空串，否则 YYYYMMDD。"""
    if v is None or pd.isna(v):
        return ""
    s = pd.Timestamp(v).strftime("%Y%m%d")
    return "" if s == _JQ_LISTED_SENTINEL else s


def _norm_date(v) -> str:
    """'YYYYMMDD' / 'YYYY-MM-DD' / date / datetime → 'YYYY-MM-DD'（jqdatasdk 的入参形态）。"""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _normalize_frame(df: pd.DataFrame, jq_code: str) -> pd.DataFrame:
    """聚宽 get_price 返回 → 统一帧（列：ts_code/trade_date/open/high/low/close/vol/amount）。

    **形状分叉**（模块 docstring 第 4 条）：单标的返回 DatetimeIndex（无 code 列），
    多标的返回带 `time`/`code` 列的平面表。此处统一为「一行一 (ts_code, trade_date)」。

    `trade_date` 输出为 **YYYYMMDD 字符串**——`tushare_adapter.validate_bar_quality` 与
    `to_save_rows` 都按该形态解析（`pd.to_datetime(..., format="%Y%m%d")`）。
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()
    d = df.copy()
    if "code" in d.columns:                      # 多标的形状
        d = d.rename(columns={"time": "date"})
        codes = d["code"].astype(str)
    else:                                        # 单标的形状：DatetimeIndex
        d["date"] = d.index
        d = d.reset_index(drop=True)
        codes = pd.Series([jq_code] * len(d), index=d.index)
    out = pd.DataFrame({
        "ts_code": [f"{c.split('.')[0]}.{_JQ_TO_TS.get(c.split('.')[-1].upper(), c.split('.')[-1])}"
                    for c in codes],
        "trade_date": pd.to_datetime(d["date"]).dt.strftime("%Y%m%d"),
        "open": pd.to_numeric(d["open"], errors="coerce"),
        "high": pd.to_numeric(d["high"], errors="coerce"),
        "low": pd.to_numeric(d["low"], errors="coerce"),
        "close": pd.to_numeric(d["close"], errors="coerce"),
        "vol": pd.to_numeric(d["volume"], errors="coerce"),     # 股，不换算
        "amount": pd.to_numeric(d["money"], errors="coerce"),   # 元，不换算
    })
    return out.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


@register_adapter
class JoinQuantAdapter(BaseDataAdapter):
    """聚宽 JQData adapter（A 股历史切片，`astock_daily_jq`）。"""

    provider = "joinquant"

    # 能力矩阵＝sync_id 级（`provider_capabilities` 的真源，经 SYNC_ID_CAP_MAP 归一）。
    # `astock_daily_jq` 是**独立 sync_id**（不占 `astock_daily` 的切换位——威廉姆 2026-10-06
    # 裁定：聚宽试用窗口无最近 3 个月，不能当 astock_daily 的常规替代源）。
    # 批 107 A 步并入 `astock_list`（→ 经 SYNC_ID_CAP_MAP 归一为 `ref_data`）——首个「非 bar 多源」。
    capabilities = {"astock_daily_jq", "astock_list"}

    # 契约层能力声明（kind 粒度，`register_adapter` import 时执法）。
    capability_decls = [
        CapabilityDecl("bar_daily", "historical", ASTOCK_ALL),
        CapabilityDecl("static_list", "historical", ASTOCK_ALL,
                       sub_kinds=frozenset({"stock"})),   # 批 107：ref_data 首批（仅股票清单）
    ]

    def __init__(self):
        self._ds = None
        # 最近一次 `pull_daily` 被**跳过**的标的（非 XSHE/XSHG、或单只取数被上游拒）——
        # 供编排层记账/日志，**不是**失败（聚宽无北交所是静态能力边界）。
        self.last_skipped: list[str] = []

    # ── 数据源句柄 ──

    def _data_source(self):
        if self._ds is None:
            from src.data_platform.data_source import get_data_source
            ds = get_data_source("joinquant")
            if ds is None:
                ds = _bare_joinquant_source()
            self._ds = ds
        return self._ds

    def get_client(self):
        """已 auth 的 jqdatasdk 模块（auth 门见模块 docstring）。"""
        return self._data_source().get_client()

    def account_window(self) -> dict:
        """账号窗口/额度（`get_account_info`/`get_query_count` 真值；窗口**动态取**）。"""
        return self._data_source().account_window()

    # ── 符号归一 ──

    def to_source_symbol(self, symbol: str) -> str:
        """内部 ts_code（`600000.SH` / `600000.SHSE`）→ 聚宽（`600000.XSHG`）。默认直通。"""
        if not symbol or "." not in str(symbol):
            return str(symbol)
        code, _, ex = str(symbol).rpartition(".")
        return f"{code}.{_TS_TO_JQ.get(ex.upper(), ex.upper())}"

    @staticmethod
    def to_internal_ts_code(jq_code: str) -> str:
        """聚宽 `000001.XSHE` → 内部 ts_code `000001.SZ`（再由 `to_vt_symbol` → `.SZSE`）。"""
        if not jq_code or "." not in str(jq_code):
            return str(jq_code)
        code, _, ex = str(jq_code).rpartition(".")
        return f"{code}.{_JQ_TO_TS.get(ex.upper(), ex.upper())}"

    # ── 拉取 ──

    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "astock",
                   symbols=None) -> pd.DataFrame:
        """未复权日线（单/多标的，统一帧——见 `_normalize_frame`）。

        - `fq=None` / `panel=False` / `skip_paused=True` **三个都显式传**（见模块 docstring）。
        - 返回列：ts_code/trade_date/open/high/low/close/vol/amount（**无 adj_factor**）。
        - 空区间/无数据 → 空 DataFrame（调用方按 empty 处理，不抛）。
        - **发请求前剔除聚宽不覆盖的交易所**（`_JQ_EXCHANGES`，如北交所 `.BJ`）；被剔的记入
          `self.last_skipped` 并打 WARNING（**不静默**）。单批取数失败走**二分降级**隔离坏标的，
          绝不因一只坏标的丢整批。
        """
        jq = self.get_client()
        syms = symbols if symbols else [symbol]
        if isinstance(syms, str):
            syms = [syms]
        self.last_skipped = []
        jq_syms = []
        for s in syms:
            if not s:
                continue
            j = self.to_source_symbol(s)
            if j.rsplit(".", 1)[-1].upper() in _JQ_EXCHANGES:
                jq_syms.append(j)
            else:
                self.last_skipped.append(j)
        if self.last_skipped:
            logger.warning("聚宽不覆盖 %d 只（非 XSHE/XSHG，如 %s）——已跳过，不参与本批",
                           len(self.last_skipped), self.last_skipped[:5])
        if not jq_syms:
            return pd.DataFrame()
        s, e = _norm_date(start), _norm_date(end)
        frames = []
        for i in range(0, len(jq_syms), _SYM_BATCH):
            chunk = jq_syms[i:i + _SYM_BATCH]
            norm = self._fetch_chunk(jq, chunk, s, e)
            if norm is not None and not norm.empty:
                frames.append(norm)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _fetch_chunk(self, jq, chunk: list[str], s: str, e: str) -> pd.DataFrame | None:
        """单批取数；失败则**二分降级**（隔离坏标的，其余照拉）。

        为什么必须二分（2026-10-06 实测教训）：jqdatasdk 对含无效标的的批次**整批拒绝**
        （`无效的证券代码 '920000.BJ'`）⇒ 只 try/except 一个批次会让 800 只一起丢，且
        adapter 只打一行 warning，编排层看不到 = **静默数据洞**。二分后坏标的被逐只隔离，
        其余 799 只照常投递，被隔离者进 `last_skipped`。
        """
        try:
            df = jq.get_price(chunk, start_date=s, end_date=e, frequency="daily",
                              fields=_FIELDS, fq=None, panel=False, skip_paused=True)
        except Exception as ex:
            if len(chunk) == 1:
                logger.warning("聚宽跳过无效标的 %s: %s", chunk[0], ex)
                self.last_skipped.append(chunk[0])
                return None
            mid = len(chunk) // 2
            left = self._fetch_chunk(jq, chunk[:mid], s, e)
            right = self._fetch_chunk(jq, chunk[mid:], s, e)
            parts = [p for p in (left, right) if p is not None and not p.empty]
            return pd.concat(parts, ignore_index=True) if parts else None
        return _normalize_frame(df, chunk[0] if len(chunk) == 1 else "")

    def pull_daily_batch(self, trade_date: str, kind: str = "astock",
                         symbols=None) -> pd.DataFrame:
        """按日全市场（聚宽无原生按日接口 → 内部按 `_SYM_BATCH` 分批 `get_price`）。

        `symbols` 为内部 ts_code 列表；缺省由调用方（engine handler）提供——
        adapter 层不持有「哪些标的在册」的业务规则（那在 `asset_static_info`/`static_symbols`）。
        """
        if kind not in ("astock",):
            raise UnsupportedFeature(f"joinquant 不支持 kind={kind} 按日批量")
        if not symbols:
            raise UnsupportedFeature("joinquant.pull_daily_batch 需要 symbols（调用方提供在册标的）")
        d = _norm_date(trade_date)
        return self.pull_daily("", d, d, kind=kind, symbols=list(symbols))

    def pull_minute(self, symbol: str, freq: str, start: str, end: str) -> pd.DataFrame:
        raise UnsupportedFeature("聚宽分钟线未实现（批 103c）")

    # ── 批 107：供给面端口（首个「非 bar 多源」样板） ──

    def fetch_supply(self, kind: str, sub_kind: str | None = None, **params) -> pd.DataFrame:
        """供给面 `(kind, sub_kind)` → **归一化 DataFrame**（列形状 = 目标表列）。

        与 `fetch`（消费/供给统一 ContractFrame）分开：本端口给**写入面**同步项用，输出
        **目标表列形状**（真源 `sync_kind_config`），由 adapter 负责「源侧 → canonical」映射
        ——这是「列形状单一真源」在多源下的落地（见 `base.py::fetch_supply` 立法）。

        现状（批 107 A 步样板）：实现 `(static_list, stock)` → `asset_static_info` 列形状
        （让 `sync_config.provider=joinquant` 对 `astock_list` 真切，不再抛 `ProviderConfigError`）。
        其余供给项对聚宽未实现 ⇒ `UnsupportedFeature`（**响亮**，非静默串源）。
        """
        if kind == "static_list" and sub_kind == "stock":
            return self._supply_static_list_stock()
        raise UnsupportedFeature(
            f"joinquant 未实现 fetch_supply(kind={kind}, sub_kind={sub_kind})")

    def _supply_static_list_stock(self) -> pd.DataFrame:
        """`get_all_securities(types=['stock'])` → `asset_static_info` 列形状。

        列序/列名 = `ts_code,name,industry,market,list_status,list_date,delist_date`（与 tushare
        `pull_stock_basic_raw` 输出同形 ⇒ 消费方 `engine._sync_astock_list` **零分支**）。

        **字段缺口（响亮声明，非静默降级）**：
        - `industry`：聚宽 `get_all_securities` **不含**行业 ⇒ 落空串 + 一次性告警
          （需行业须另调 `get_industry`，本样板不取）。
        - `market`：聚宽无「市场板块」字段 ⇒ 由代码前缀**确定性推导**（见 `_board_of_code`）。
        - `delist_date`：聚宽哨兵 `2200-01-01`＝未退市 ⇒ 归一空串（见 `_delist_or_blank`）。
        """
        jq = self.get_client()
        df = jq.get_all_securities(types=["stock"], date=None)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        ts_codes = [f"{str(c).split('.')[0]}.{_JQ_TO_TS.get(str(c).split('.')[-1].upper(), str(c).split('.')[-1])}"
                    for c in df.index]
        names = (df["display_name"] if "display_name" in df.columns else df["name"]).astype(str)
        start = pd.to_datetime(df["start_date"]) if "start_date" in df.columns else pd.Series([pd.NaT] * len(df))
        end = pd.to_datetime(df["end_date"]) if "end_date" in df.columns else pd.Series([pd.NaT] * len(df))
        if not getattr(self, "_industry_gap_warned", False):
            logger.warning("joinquant static_list/stock 不提供 `industry`（get_all_securities 无该字段）"
                           "——asset_static_info.industry 将落空串；如需行业须另接 get_industry")
            self._industry_gap_warned = True
        return pd.DataFrame({
            "ts_code": ts_codes,
            "name": list(names),
            "industry": "",                              # 缺口（响亮声明，见 docstring）
            "market": [_board_of_code(c) for c in ts_codes],
            "list_status": "L",
            "list_date": start.dt.strftime("%Y%m%d").fillna("").tolist(),
            "delist_date": [_delist_or_blank(v) for v in end],
        })

    def to_bar_rows(self, df: pd.DataFrame, freq: str, adj_map: dict | None = None) -> list[tuple]:
        """统一帧 → 11 字段落库行 `(symbol, freq, ts, o,h,l,c, volume, amount, adj_factor, source)`。

        - `symbol` = vt_symbol（`to_vt_symbol('000001.SZ') → '000001.SZSE'`）——A 股无源后缀，
          与 tushare 行**同键**（威廉姆裁定「同表同键允许互写」）。
        - `adj_factor` **恒 None**（见模块 docstring 第 6 条）：不做跨源换算，
          由落库侧 COALESCE 保住 tushare 已回填因子。
        - `volume/amount` 不换算（第 5 条）。
        """
        from src.data_platform.schema import to_vt_symbol
        from src.data_platform.tz import as_utc

        rows: list[tuple] = []
        if df is None or len(df) == 0:
            return rows
        for r in df.to_dict("records"):
            ts_code = str(r.get("ts_code") or "")
            td = r.get("trade_date")
            if not ts_code or td is None or str(td) in ("", "NaT", "nan"):
                continue
            vt = to_vt_symbol(ts_code)
            ts = as_utc(pd.Timestamp(str(td)).to_pydatetime())
            rows.append((
                vt, freq, ts,
                float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                float(r["vol"]), float(r["amount"]),
                None,            # adj_factor：跨源基准不同，不写（见 §0.4）
                self.provider,
            ))
        return rows


def _bare_joinquant_source():
    """无 DB 行时的裸构造（.env `JQDATA_USER`/`JQDATA_PASSWORD` 路径，与 tushare 同语义）。"""
    from src.data_platform.data_source import JoinQuantDataSource
    return JoinQuantDataSource()
