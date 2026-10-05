"""币安 USDⓈ-M 永续公开数据 adapter（批 101；**非实时**）。

**可达性前提**（2026-10-06 prod 实测，证据见 `flow/方案/crypto数据层-立项设计-20261006.md`）：
- ✅ `data.binance.vision`——官方**批量历史 K 线 ZIP**（`futures/um` = USDⓈ-M 合约，含永续）
- ✅ `s3.ap-northeast-1.amazonaws.com/data.binance.vision`——S3 ListObjectsV2（符号枚举）
- ❌ `fapi.binance.com`——永续**实时**/下单端点，**网络阻断**（需境外 relay，非本 adapter 范围）

**能力边界**：`capabilities` 只含 `crypto_perp_daily`（历史批量）；**不含 `rt_quote`**——
批量站给的是 T+1 静态文件，虚报实时会让 resolve 把实盘决策路由到一个没有实时能力的源。

**T+1 语义**（实测）：UTC 当日文件 404、前一日 200 ⇒ 窗口上界由 handler 定为 UTC 昨日。
**月包起点**（实测）：月包自 2020-01 起（BTCUSDT 2019-09/10 → 404）⇒ 更早区间自动回退日包。

**落库形状**：`fetch_supply` 返回**源原生 DataFrame**（批 100 端口语义），字段映射由
`to_bar_rows` 承担；`ts` 取 **open_time**（bar 起始，UTC aware）——与 A 股日线「ts=本地零时」
口径同构（`db.validate_bars` 对 naive 会按上海解释，故**必须** aware UTC）。
"""
from __future__ import annotations

import csv
import io
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from src.quant_common.contract import CRYPTO_ALL, CapabilityDecl

from .base import BaseDataAdapter, UnsupportedFeature, register_adapter

logger = logging.getLogger("quant")

_BATCH = "https://data.binance.vision"
_S3 = "https://s3.ap-northeast-1.amazonaws.com/data.binance.vision"
_UM = "futures/um"
_VENUE = "BINANCE"          # symbol 后缀（security_master._market_of_suffix 认它 → crypto）
_TIMEOUT = 30
_MAX_WORKERS = 8            # 批量站是静态文件 CDN（无 weight 模型），礼貌性靠小并发表达

# 币安批量 K 线 CSV 12 列（实测）；旧文件无表头，解析时探测
_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
         "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]

_MONTHLY_FROM = (2020, 1)                    # 实测：月包自 2020-01（2019-09/10 → 404）
_FLOOR_DATE = date(2020, 1, 1)               # 月包 floor 的 date 形态（pull_daily 省流阀的边界判据）
_DELIVERY_RE = re.compile(r"_[0-9]{6}$")     # BTCUSDT_210129 = 交割合约（非永续，排除）


def _get(url: str, timeout: int = _TIMEOUT) -> bytes | None:
    """GET 原始字节；404 → None（文件不存在＝正常态，不是错误：未上市/未到账/停牌）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "quant-data-sync/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _kline_url(sym: str, interval: str, period: str, key: str) -> str:
    """period='daily' → key=YYYY-MM-DD；'monthly' → key=YYYY-MM。非 ASCII 符号须百分号编码
    （实测存在中文名符号如 `币安人生USDT`，URL 编码后方可下载）。"""
    q = urllib.parse.quote(sym, safe="")
    return f"{_BATCH}/data/{_UM}/{period}/klines/{q}/{interval}/{q}-{interval}-{key}.zip"


def _parse_kline_csv(blob: bytes) -> pd.DataFrame:
    """ZIP → DataFrame（12 列；兼容有无表头两种历史形态）。

    **只剔「不可解析」行**（open_time/ohlc 为 NaN）；`ohlc=0` 的坏行**不在此剔**——
    那是写入门 `db.validate_bars` 的单一职责（此处再剔＝两处口径，日后必漂移）。
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0] and rows[0][0] == "open_time":
        rows = rows[1:]
    df = pd.DataFrame(rows, columns=_COLS)
    for c in ("open_time", "close_time"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open_time", "open", "high", "low", "close"])


def _period_keys(start: date, end: date) -> list[tuple[str, str]]:
    """区间 → [(period, key)]：**完整月**用 monthly（且 ≥2020-01），其余逐日用 daily。

    月包粒度把 7 年回补从「2550 个日文件/标的」压到「81 个月文件/标的」；月包起点前
    （2019-09~2019-12）与当月不完整区间自动回退日包。
    """
    out: list[tuple[str, str]] = []
    cur = start
    while cur <= end:
        # 当月最后一天（下月 1 号回退 1 天）
        month_end = (cur.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if cur.day == 1 and month_end <= end and (cur.year, cur.month) >= _MONTHLY_FROM:
            out.append(("monthly", cur.strftime("%Y-%m")))
            cur = month_end + timedelta(days=1)
        else:
            out.append(("daily", cur.strftime("%Y-%m-%d")))
            cur += timedelta(days=1)
    return out


def _to_date(s: str) -> date:
    """'YYYYMMDD' → date。"""
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


@register_adapter
class BinanceAdapter(BaseDataAdapter):
    """币安 USDⓈ-M 永续公开数据（批量历史；非实时）。"""

    provider = "binance"
    # 供给项（sync_id）：本 adapter 只服务加密永续日线（批 101）
    capabilities = {"crypto_perp_daily"}
    # 契约层能力声明（粒度＝kind）——只声明**真实现**的能力：bar_daily（historical，crypto 全域）。
    # bar_daily 非聚合域 ⇒ sub_kinds 必须为空（contract.validate_capability_decls 硬约束）。
    capability_decls = [CapabilityDecl("bar_daily", "historical", CRYPTO_ALL)]

    def __init__(self):
        self._symbols: list[str] | None = None

    # ——— 符号主体（S3 ListObjectsV2 分页；fapi 被墙时唯一权威枚举通道）———

    def list_symbols(self, refresh: bool = False) -> list[str]:
        """全部**永续**符号（USDT/USDC/BUSD 各报价），排除 `_YYMMDD` 交割合约。进程内缓存。

        实测（2026-10-06）：1056 总条目 = 1004 永续 + 52 交割合约；含 5 个中文名符号。
        """
        if self._symbols is not None and not refresh:
            return self._symbols
        found: set[str] = set()
        token: str | None = None
        for _ in range(40):                      # 防御上限（实测 2 页）
            q = f"{_S3}?list-type=2&prefix=data/{_UM}/monthly/klines/&delimiter=/&max-keys=1000"
            if token:
                q += "&continuation-token=" + urllib.parse.quote(token, safe="")
            blob = _get(q)
            if not blob:
                break
            xml = blob.decode("utf-8", "replace")
            found.update(m.split("/")[-2]
                         for m in re.findall(r"<CommonPrefixes><Prefix>(.*?)</Prefix>", xml))
            m = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", xml)
            if not m:
                break
            token = m.group(1)
        self._symbols = sorted(s for s in found if s and not _DELIVERY_RE.search(s))
        return self._symbols

    def _month_ok(self, sym: str, idx: int) -> bool:
        y = _MONTHLY_FROM[0] + idx // 12
        m = _MONTHLY_FROM[1] + idx % 12
        return _get(_kline_url(sym, "1d", "monthly", f"{y:04d}-{m:02d}"),
                    timeout=15) is not None

    def first_available_month(self, sym: str) -> tuple[int, int] | None:
        """二分探测该符号**最早可得月包**（返回 (年, 月)；无月包返回 None）。

        长区间回补的省流阀：无此探测则新上市标的会白打几十次 404（实测 1004 标的 × 81 月）。
        """
        today = date.today()
        hi = (today.year - _MONTHLY_FROM[0]) * 12 + (today.month - _MONTHLY_FROM[1])
        if hi < 0:
            return None
        # 当月通常无月包（不完整）⇒ 从上一月起探
        if not self._month_ok(sym, hi):
            hi -= 1
            if hi < 0 or not self._month_ok(sym, hi):
                return None
        lo = 0
        while lo < hi:
            mid = (lo + hi) // 2
            if self._month_ok(sym, mid):
                hi = mid
            else:
                lo = mid + 1
        return _MONTHLY_FROM[0] + lo // 12, _MONTHLY_FROM[1] + lo % 12

    # ——— 拉取 ———

    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "crypto") -> pd.DataFrame:
        """per-symbol 日线（symbol 可带/不带 `.BINANCE`；start/end = 'YYYYMMDD'）。

        长区间（>90 天）先探测最早**月包**，把起点抬到有数据处——省掉成片 404 空跑。

        ⚠️ **省流阀不得裁掉 pre-floor 日包区间**：月包自 `_MONTHLY_FROM` 起，而日包更早
        （`start_floor='2019-09-08'` 的 USDT-M 上线日就在月包 floor 之前）。若无条件
        `s = max(s, 月包起点)`，请求起点早于 floor 时会被抬到 floor ⇒ **静默丢掉
        2019-09~2019-12 的逐日历史**（且 config 里声明的 start_floor 变成谎言）。
        故：仅当请求起点已 ≥ floor、或探到的月包起点**晚于** floor（＝该符号上线晚）时才上抬。
        """
        sym = str(symbol).split(".")[0]
        s, e = _to_date(start), _to_date(end)
        if s > e:
            return pd.DataFrame()
        if (e - s).days > 90:
            probe = self.first_available_month(sym)
            if probe:
                pstart = date(probe[0], probe[1], 1)
                if s < _FLOOR_DATE:
                    # 起点在 floor 之前：只有探到「晚于 floor 的月包起点」（＝上线晚）才上抬；
                    # 探到的恰是 floor 当月 ⇒ 该符号可能自 2019 即有数据，保留原起点走日包。
                    if pstart > _FLOOR_DATE:
                        s = pstart
                else:
                    s = max(s, pstart)
            else:
                s = max(s, e - timedelta(days=30))
        keys = _period_keys(s, e)
        frames: list[pd.DataFrame] = []
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            for blob in ex.map(lambda pk: _get(_kline_url(sym, "1d", pk[0], pk[1])), keys):
                if blob:
                    frames.append(_parse_kline_csv(blob))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df["binance_symbol"] = sym
        return df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

    def pull_minute(self, symbol: str, freq: str, start: str, end: str) -> pd.DataFrame:
        """分钟线留批 101b（同 adapter，仅加 interval 映射与 freq→表映射）。"""
        raise NotImplementedError("BinanceAdapter.pull_minute 未实现（批 101b）")

    def to_bar_rows(self, df: pd.DataFrame, freq: str, adj_map: dict | None = None) -> list[tuple]:
        """→ 统一 11 字段 (symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)。

        - symbol = 源符号 + `.BINANCE`（vt_symbol 形态，`security_master._market_of_suffix` 认）
        - ts = **open_time**（bar 起始，UTC aware——naive 会被 db.validate_bars 按上海解释，故必须 aware）
        - volume = base 数量；amount = quote_volume（计价额）
        - adj_factor = None（加密无复权概念）
        """
        rows: list[tuple] = []
        if df is None or df.empty:
            return rows
        for r in df.to_dict("records"):
            sym = str(r.get("binance_symbol") or r.get("symbol") or "")
            if not sym:
                continue
            if not sym.upper().endswith("." + _VENUE):
                sym = f"{sym}.{_VENUE}"
            ts = datetime.fromtimestamp(int(r["open_time"]) / 1000, tz=timezone.utc)
            rows.append((
                sym, freq, ts,
                float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                float(r["volume"]), float(r["quote_volume"]),
                None, self.provider,
            ))
        return rows

    def fetch_supply(self, kind: str, sub_kind: str | None = None, **params) -> pd.DataFrame:
        """批 100 供给端口：`('bar_daily', 'perp')` → 逐标的区间批量拉取（源原生 DataFrame）。

        参数：`symbol`（源符号，可带 .BINANCE）/ `start` / `end`（'YYYYMMDD'）。
        本 adapter 的 `capabilities` 是 sync_id 级（crypto_perp_daily），端口这里按 kind 分派，
        与 tushare 侧同构——证明批 100 的端口**与 provider 无关**。
        """
        if kind == "bar_daily" and sub_kind in (None, "perp"):
            return self.pull_daily(params.get("symbol", ""),
                                   params.get("start", ""), params.get("end", ""))
        raise UnsupportedFeature(
            f"binance 未实现 fetch_supply(kind={kind}, sub_kind={sub_kind})")
