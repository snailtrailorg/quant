"""币安 USDⓈ-M 永续公开数据 adapter（批 101；**非实时**）。

**可达性前提**（2026-10-06 prod 实测，证据见 `flow/方案/crypto数据层-立项设计-20261006.md`）：
- ✅ `data.binance.vision`——官方**批量历史 K 线 ZIP**（`futures/um` = USDⓈ-M 合约，含永续）
- ✅ `s3.ap-northeast-1.amazonaws.com/data.binance.vision`——S3 ListObjectsV2（符号枚举）
- ❌ `fapi.binance.com`——永续**实时**/下单端点，**网络阻断**（需境外 relay，非本 adapter 范围）

**能力边界**：`capabilities` 只含 `binance_perp_daily`（历史批量）；**不含 `rt_quote`**——
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
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

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
_FLOOR_DATE = date(2020, 1, 1)               # 月包 floor 的 date 形态（_pull 省流阀的边界判据）
_DELIVERY_RE = re.compile(r"_[0-9]{6}$")     # BTCUSDT_210129 = 交割合约（非永续，排除）

# 批 108·步 1：**源可达下界**（窗口第四边界的真源）——与上面两个「批量路径优化边界」**不同义**：
#   实测（2026-10-07 主会话探 data.binance.vision）：**日包** `2019-12-30`→404 / `2019-12-31`→200；
#   四个 interval（1d/1h/1m/15m）**同值**。月包自 2020-01（更晚）⇒ 日包才是真实下界。
#   `2019-09-08`＝USDT-M **上线日**（≠ 源可得）；`2020-01-01`＝月包 floor。
#   ⇒ 用后两者当源界会让 §5.3 的 unreachable 段**永不触发**（永久 phantom gap，双盲审② A2 P1-a）。
_SOURCE_EARLIEST = "2019-12-31"

# 内部 freq → 批量站 interval 目录名（**映射真源**；`to_source_freq` 暴露给契约层）。
# freq 同时是 `bar_{freq.lower()}` 表名后缀（db.save_bars 的 insert 模板即如此）——
# 故 '1D'→bar_1D / '1h'→bar_1h / '1min'→bar_1min / '15min'→bar_15min 一一对应（PG 折叠大小写）。
_INTERVAL_BY_FREQ: dict[str, str] = {
    "1D": "1d", "1d": "1d",
    "1h": "1h", "1H": "1h",
    "1min": "1m",
    "15min": "15m",
}


def _proxies(proxy: str | None) -> dict | None:
    """requests 的 `proxies` 参数；None ＝ 直连。

    批 102b 收编：真源迁到 `data_platform/proxy.py::proxies_map`（okx 等新 adapter 直接用
    那个）——本函数只留名字做薄转发，**不再自持一份 map 语义**（两处各写一遍必日后漂移）。
    """
    from src.data_platform.proxy import proxies_map
    return proxies_map(proxy)


def _get(url: str, timeout: int = _TIMEOUT, proxy: str | None = None) -> bytes | None:
    """GET 原始字节；404 → None（文件不存在＝正常态，不是错误：未上市/未到账/停牌）。

    批 102a：由 `urllib` 迁到 **requests**——唯一动机＝代理出口（urllib 没有 socks/http
    代理的统一接线）。**行为等价**：404 → None；其余 4xx/5xx → 抛 `requests.HTTPError`。
    """
    resp = requests.get(url, headers={"User-Agent": "quant-data-sync/1.0"},
                        timeout=timeout, proxies=_proxies(proxy))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def _kline_url(sym: str, interval: str, period: str, key: str, base: str = _BATCH) -> str:
    """period='daily' → key=YYYY-MM-DD；'monthly' → key=YYYY-MM。非 ASCII 符号须百分号编码
    （实测存在中文名符号如 `币安人生USDT`，URL 编码后方可下载）。

    `base` 默认 `_BATCH`；批 102a 可由 `proxy_binding.endpoint_override` 覆盖（镜像站/换区域）。
    """
    q = urllib.parse.quote(sym, safe="")
    return f"{base}/data/{_UM}/{period}/klines/{q}/{interval}/{q}-{interval}-{key}.zip"


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
    # 供给项（sync_id）：币安永续公开数据——日线（批 101）+ 小时/1min/15min（批 101b）
    capabilities = {"binance_perp_daily", "binance_perp_hourly",
                    "binance_perp_1min", "binance_perp_15min"}
    # 契约层能力声明（粒度＝kind）——只声明**真实现**的能力：crypto 全域的日线 + 盘中 bar。
    # ⚠️ 盘中 bar 三种粒度（1h/1min/15min）**都归 `bar_minute`**：27 词 DataKind 立法里
    # **没有 hour 粒度词**，而 `bar_minute` 的合法时态含 historical（盘中 K 线族）。
    # 加新 DataKind 是立法动作（contract 27 词 + KIND_CAP_CLASS + 三条钉），本批不做——
    # 故 1h 在 kind 维**名义收窄**为 bar_minute，落表靠 `sync_kind_config.pg_table` 区分
    # （bar_1h / bar_1min / bar_15min，与 freq 互为大小写同形）。
    # 非聚合域 ⇒ sub_kinds 必须为空（contract.validate_capability_decls 硬约束）。
    capability_decls = [
        CapabilityDecl("bar_daily", "historical", CRYPTO_ALL),
        CapabilityDecl("bar_minute", "historical", CRYPTO_ALL),
    ]

    # 批 102a：本 adapter 自己发 HTTP（`_get` 走 requests）⇒ 出口可注入代理 / 端点覆盖。
    supports_exit_config = True

    def __init__(self):
        self._symbols: list[str] | None = None

    def _exit(self) -> tuple[str | None, str]:
        """→ `(proxy, kline_base)`。

        端点覆盖（`proxy_binding.endpoint_override`）**只作用于 K 线下载基址**（`_BATCH`）；
        符号枚举走的是 Amazon S3 的固定入口（`_S3`），**不随覆盖**——换 S3 区域是另一维，
        留待真实需求（挂账，见任务文件 §挂账）。代理对两条通道都生效。
        """
        proxy, endpoint = self.exit_config()
        return proxy, (endpoint or _BATCH)

    # ——— 符号主体（S3 ListObjectsV2 分页；fapi 被墙时唯一权威枚举通道）———

    def list_symbols(self, refresh: bool = False) -> list[str]:
        """全部**永续**符号（USDT/USDC/BUSD 各报价），排除 `_YYMMDD` 交割合约。进程内缓存。

        实测（2026-10-06）：1056 总条目 = 1004 永续 + 52 交割合约；含 5 个中文名符号。
        """
        if self._symbols is not None and not refresh:
            return self._symbols
        found: set[str] = set()
        token: str | None = None
        proxy, _ = self._exit()
        for _ in range(40):                      # 防御上限（实测 2 页）
            q = f"{_S3}?list-type=2&prefix=data/{_UM}/monthly/klines/&delimiter=/&max-keys=1000"
            if token:
                q += "&continuation-token=" + urllib.parse.quote(token, safe="")
            blob = _get(q, proxy=proxy)
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
        proxy, kb = self._exit()
        return _get(_kline_url(sym, "1d", "monthly", f"{y:04d}-{m:02d}", base=kb),
                    timeout=15, proxy=proxy) is not None

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

    # ——— 源可达区间（批 108·步 1）———

    def available_range(self, kind: str) -> tuple[str | None, str | None]:
        """见基类契约。**实测取值**（2026-10-07 主会话探 `data.binance.vision`）：

        - `earliest` ＝ **`2019-12-31`**——USDⓈ-M 批量集实际最早**日包**（`2019-12-30`→404 /
          `2019-12-31`→200）；**四个 interval 一致**（`1d`/`1h`/`1m`/`15m` 逐一同测）。
          三个易混值须辨（设计 §5.1）：`2019-09-08` ＝ **上线日**（≠ 批量源可得）；
          `2020-01`/`_FLOOR_DATE` ＝ **月包** floor（仅批量路径的优化边界）——**都不是本源下界**。
        - `latest` ＝ `None`（随今日滚动）；实际可用上界另受 `publish_lag` 与游标契约约束。
        """
        return (_SOURCE_EARLIEST, None)

    def publish_lag(self, kind: str) -> int:
        """1 自然日（对齐批量站 T+1 发布：UTC 当日文件 404、前一日 200）。

        实测补充（批 101b）：上一日的包在 UTC 早间**可能尚未发布**（北京时间 10:46 时 `D-1`
        在全部 interval 上仍 404）。该发布**抖动**不由本值承担，而由 crypto handler 的
        **游标契约**（游标＝实际取到数据的最后一日）兜住——两处是**分工**不是重复。
        """
        return 1

    # ——— 拉取 ———

    def _pull(self, sym: str, interval: str, start: str, end: str) -> pd.DataFrame:
        """区间拉取（interval = `1d`/`1h`/`1m`/`15m`；start/end = 'YYYYMMDD'）。

        长区间（>90 天）先探测最早**月包**，把起点抬到有数据处——省掉成片 404 空跑。
        月包探测统一用 `1d` 月包（最便宜，且各 interval 的上线月相同——同一合约同刻上市）。

        ⚠️ **省流阀不得裁掉 pre-floor 日包区间**：月包自 `_MONTHLY_FROM` 起，而日包更早
        （`start_floor='2019-09-08'` 的 USDT-M 上线日就在月包 floor 之前）。若无条件
        `s = max(s, 月包起点)`，请求起点早于 floor 时会被抬到 floor ⇒ **静默丢掉
        2019-09~2019-12 的逐日历史**（且 config 里声明的 start_floor 变成谎言）。
        故：仅当请求起点已 ≥ floor、或探到的月包起点**晚于** floor（＝该符号上线晚）时才上抬。
        """
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
        proxy, kb = self._exit()                 # 出口配置一次解析，供本区间全部下载复用
        frames: list[pd.DataFrame] = []
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            for blob in ex.map(
                    lambda pk: _get(_kline_url(sym, interval, pk[0], pk[1], base=kb),
                                    proxy=proxy), keys):
                if blob:
                    frames.append(_parse_kline_csv(blob))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df["binance_symbol"] = sym
        return df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "crypto") -> pd.DataFrame:
        """per-symbol 日线（symbol 可带/不带 `.BINANCE`；start/end = 'YYYYMMDD'）。"""
        return self._pull(str(symbol).split(".")[0], "1d", start, end)

    def pull_minute(self, symbol: str, freq: str, start: str, end: str) -> pd.DataFrame:
        """per-symbol 分钟/小时线（批 101b）：freq（内部 `1min`/`15min`/`1h`）→ interval。

        - 只认**内部 freq**：不支持/未映射/日粒度（走 `pull_daily`）/源 interval 名（如 `15m`）
          一律 `UnsupportedFeature`（**响亮**，不是空帧——空帧会被上游当「该窗口无数据」
          静默记账；而放行 `15m` 会让 `to_bar_rows` 把 `15m` 当 freq 写进列、`bar_15m`
          表也不存在，等于把调用方 bug 变成脏数据）。
        - start/end 容忍 `'YYYYMMDD HH:MM:SS'` 形态（基类分钟契约如此；本源只需日期）——
          取空格前段，避免调用方被迫改写。
        - 文件 404（未发布/该日无包）→ 空帧，**不是异常**（T+1 滞后是常态，见模块 docstring）。
        """
        if freq not in _INTERVAL_BY_FREQ or _INTERVAL_BY_FREQ[freq] == "1d":
            raise UnsupportedFeature(
                f"binance pull_minute 不支持 freq={freq!r}（支持 {sorted(_INTERVAL_BY_FREQ)}）")
        interval = _INTERVAL_BY_FREQ[freq]
        return self._pull(str(symbol).split(".")[0], interval,
                          str(start).split(" ")[0], str(end).split(" ")[0])

    def to_source_freq(self, freq: str) -> str:
        """内部 freq → 源 interval 目录名（`1min`→`1m`）。未映射直通（由调用方判合法性）。"""
        return _INTERVAL_BY_FREQ.get(freq, freq)

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
        """批 100 供给端口：`('bar_daily'|'bar_minute', 'perp')` → 逐标的区间批量拉取（源原生 DataFrame）。

        参数：`symbol`（源符号，可带 .BINANCE）/ `start` / `end`（'YYYYMMDD'）；
        `bar_minute` 另收 `freq`（内部 freq，`1min`/`15min`/`1h`——由本 adapter 映射到 interval，
        引擎不持有源 interval 词表）。
        本 adapter 的 `capabilities` 是 sync_id 级（binance_perp_*），端口这里按 kind 分派，
        与 tushare 侧同构——证明批 100 的端口**与 provider 无关**。
        """
        if kind == "bar_daily" and sub_kind in (None, "perp"):
            return self.pull_daily(params.get("symbol", ""),
                                   params.get("start", ""), params.get("end", ""))
        if kind == "bar_minute" and sub_kind in (None, "perp"):
            return self.pull_minute(params.get("symbol", ""), params.get("freq", ""),
                                    params.get("start", ""), params.get("end", ""))
        raise UnsupportedFeature(
            f"binance 未实现 fetch_supply(kind={kind}, sub_kind={sub_kind})")
