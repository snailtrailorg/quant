"""OKX 永续公开数据 adapter（批 102b；**非实时**，**经代理出口**）。

**两把键**（勿混）：`okx` ＝**数据源**（本 adapter，公共行情 0 密钥）；`okx_perp` ＝**交易通道**
（下单/实时，`interfaces/okx_perp.py` 的三凭证桩）。混用会让 `_get_supply_adapter` 找不到
adapter 而静默回落 tushare 拉错源——与 binance / binance_perp 同一族教训。

**可达性前提**（2026-10-06 实测）：dev 机**出不到** OKX（直连超时；AWS 代理 `snailtrail.org:12345`
TCP 不可连，只开给 prod）⇒ 本 adapter 的正确用法**必须经代理**（102a 的 `proxy_binding`）。
`supports_exit_config=True` 使其自动纳入 `_apply_exit_config` 的注入面。

**语义三处（设计 §4.3，本批最易踩错）**：
1. **日界**：OKX `bar=1D` 是 **UTC+8 日界**，币安日线是 UTC 日界 ⇒ 本 adapter 一律用
   **`bar=1Dutc`** 统一 UTC 锚定。**禁止两源日界不同裸混一张 `bar_1d`。**
   （`1Dutc` 是否被 API 接受＝**待 prod 实测项**，见任务文件「待验」；不可用时设计 §4.3 的备选
   是 `1Hutc` 聚合——本批不预置那条路，避免为未证伪的假设加分支。）
2. **成交量**：candles 返回 `[ts,o,h,l,c,vol,volCcy,volQuote,confirm]`，SWAP 的 `vol`＝**张数**
   （各标的 `ctVal` 不同，直接用是垃圾）⇒ 映射 **`volume=volCcy`（币量）**、**`amount=volQuote`**。
3. **符号**：`<instId>.OKX`（如 `BTC-USDT-SWAP.OKX`）——源原生 instId + 后缀，**不做跨所归一**：
   它与 `BTCUSDT.BINANCE` 是两个不同标的，永不互认（`security_master._market_of_suffix` 已认 `OKX`）。

**限频**：IP 级 **20 req / 2s**（代理出口 IP 共享）⇒ 客户端滚动窗口节流 + 429/5xx/`code=50011`
退避重试。**不接 `rate_limit_context`**（同 BinanceDataSource 的理由：`_get_rate_ds('okx')` 若回落
tushare 兜底源会把 OKX 的请求计进 tushare 的熔断器＝串源）。
"""
from __future__ import annotations

import logging
import random
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from src.data_platform.proxy import proxies_map
from src.quant_common.contract import CRYPTO_ALL, CapabilityDecl

from .base import BaseDataAdapter, UnsupportedFeature, register_adapter

logger = logging.getLogger("quant")

_BASE = "https://www.okx.com"      # 缺省基址；可由 proxy_binding.endpoint_override 覆盖（102a）
_SYMBOL_SUFFIX = ".OKX"            # vt_symbol 后缀（security_master._market_of_suffix 认 → crypto）
_UA = "quant-data-sync/1.0"
_TIMEOUT = 30

_INSTRUMENTS = "/api/v5/public/instruments"       # ?instType=SWAP
_CANDLES = "/api/v5/market/history-candles"       # ?instId=&bar=&after=&limit=
_BAR = "1Dutc"                                    # **UTC 日界**（见模块 docstring 语义①）

# 批 108·步 1：**源可达下界**（窗口第四边界的真源）＝**迁移 `0137` 的 prod 实测值**——
# 「K 线 API 在 2019 全空」，故 floor 取实测 K 线边界，**不臆造**。
# ⚠️ 与 `listTime`（真上币日）**不同义**：后者报 BTC/ATOM `2019-11-12`、FIL `2019-08-10`，
#    两者差 ≈2 月（设计 §2.6/§九.3 的实证样本）。inception 取 listTime，源界取本值，禁互替。
_SOURCE_EARLIEST = "2020-01-01"

_LIMIT = 100                                      # history-candles 单页上限（接口硬上限）
_MAX_PAGES = 600                                  # 防御上限（6 年日线 ≈ 22 页，600 远超需求）
_RATE_MAX = 20                                    # IP 级 20 req…
_RATE_WINDOW = 2.0                                # …/ 2s（设计 §4.2）
_RETRY_BASE = 2.0                                 # 退避基数（秒）：2 / 4 / 8
_MAX_RETRY = 3
_RETRY_JITTER = 0.3                               # 退避抖动上限（±0–30%，批 105）
_POOL_CONNS = 2                                   # urllib3 连接池：本 adapter 单线程顺序请求
_POOL_MAXSIZE = 4
_RETRYABLE_CODE = "50011"                         # 业务层限频（HTTP 仍 200）

# candles 返回列（实测 9 列；`confirm` 缺失的历史形态按已收盘处理）
_CANDLE_COLS = ["ts", "open", "high", "low", "close", "vol", "volCcy", "volQuote", "confirm"]


def _ms(d: date) -> int:
    """date → 毫秒 epoch（UTC 零时）。"""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _to_date(s: str) -> date:
    """'YYYYMMDD'（或带时间段的形态，取前 8 位）→ date。"""
    s = str(s).split(" ")[0]
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _retry_after(resp) -> float | None:
    """`Retry-After` 头（秒）；缺失/非法 → None。"""
    raw = resp.headers.get("Retry-After") if getattr(resp, "headers", None) else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _backoff(attempt: int) -> float:
    """指数退避 **+ 抖动**：`2/4/8s` × (1 + U(0, 0.3))。

    抖动是**必要的不是装饰**（批 105）：102b prod 首跑 7/485 标的 `Connection reset`——
    全部标的按同一节奏重试时，重试流量自己排成同步波峰，正好再撞同一代理出口的
    连接表/限流；抖动把波峰打散。
    """
    return _RETRY_BASE * (2 ** attempt) * (1 + random.uniform(0, _RETRY_JITTER))


@register_adapter
class OkxAdapter(BaseDataAdapter):
    """OKX USDⓈ-M 永续公开数据（`history-candles`；非实时）。"""

    provider = "okx"
    venue = "OKX"             # 批 108·步 2：vt_symbol 后缀（与 `_SYMBOL_SUFFIX` 同值）
    # 供给项（sync_id）：本批只做日线；盘中 bar / 实时腿留后续（设计 §4.5：实时未做不动）
    capabilities = {"okx_perp_daily"}
    # 契约层能力声明（粒度＝kind）。bar_daily 非聚合域 ⇒ sub_kinds 必须为空
    # （contract.validate_capability_decls 硬约束）。
    capability_decls = [
        CapabilityDecl("bar_daily", "historical", CRYPTO_ALL),
    ]

    # 批 102a：本 adapter 自己发 HTTP（`requests`）⇒ 出口可注入代理 / 端点覆盖。
    # **本条是 OKX 在 prod 可用的前提**（dev/prod 直连 OKX 均不可达，必须走代理出口）。
    supports_exit_config = True

    def __init__(self):
        self._symbols: list[str] | None = None
        self._insts: list[dict] | None = None     # 批 108·步 2：instruments 响应缓存（list_symbols/symbol_inception 共用）
        self._req_times: deque[float] = deque()   # 滚动窗口节流的时间戳（实例级；每任务新建）
        # 批 105：会话级连接复用——原实现每请求新建 TCP+TLS(+SOCKS) 连接，485 标的 × 多页
        # ＝ 数千次握手全压同一代理出口（连接表/限流），是 `Connection reset` 的第一嫌疑。
        # 会话让连接在任务生命周期内复用（keep-alive），握手数降约两个数量级。
        self._session = requests.Session()
        _pool = HTTPAdapter(pool_connections=_POOL_CONNS, pool_maxsize=_POOL_MAXSIZE)
        self._session.mount("https://", _pool)
        self._session.mount("http://", _pool)

    # ——— 出口配置（102a 注入）———

    def _exit(self) -> tuple[str | None, str]:
        """→ `(proxy, base)`。**枚举与历史两条通道同基址**（都在 `www.okx.com`）——
        与 binance 不同（那边符号枚举走固定 S3 入口、不随覆盖），故本 adapter 的端点覆盖
        对两条通道一并生效。"""
        proxy, endpoint = self.exit_config()
        return proxy, (endpoint or _BASE)

    # ——— 节流 + 请求（含退避重试）———

    def _wait_slot(self) -> None:
        """滚动窗口节流：保证「最近 `_RATE_WINDOW` 秒内 ≤ `_RATE_MAX` 次」。"""
        while True:
            now = time.monotonic()
            while self._req_times and now - self._req_times[0] > _RATE_WINDOW:
                self._req_times.popleft()
            if len(self._req_times) < _RATE_MAX:
                self._req_times.append(now)
                return
            time.sleep(max(_RATE_WINDOW - (now - self._req_times[0]), 0.01))

    def _request(self, path: str, params: dict) -> dict:
        """GET `path`（经代理出口，**会话内连接复用**）→ 返回 JSON body；`code!='0'` **响亮抛**。

        重试面（三次退避 `_backoff`＝2/4/8s×(1+U(0,0.3))）：**网络异常**、**HTTP 429/5xx**
        （优先用 `Retry-After`，该值由服务端给出、不加抖动）、**业务限频 `code=50011`**
        （HTTP 仍 200，实现时最易漏的一路）。重试耗尽即抛，**不静默返回空**——空帧会被
        上游当「该窗口无数据」记 success，把故障埋掉。
        """
        proxy, base = self._exit()
        url = f"{base}{path}"
        last: Exception | None = None
        for attempt in range(_MAX_RETRY + 1):
            self._wait_slot()
            try:
                resp = self._session.get(url, params=params, timeout=_TIMEOUT,
                                         headers={"User-Agent": _UA}, proxies=proxies_map(proxy))
            except requests.RequestException as e:
                last = e
                if attempt >= _MAX_RETRY:
                    raise
                time.sleep(_backoff(attempt))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = requests.HTTPError(f"okx HTTP {resp.status_code}")
                if attempt >= _MAX_RETRY:
                    raise last
                time.sleep(_retry_after(resp) or _backoff(attempt))
                continue
            resp.raise_for_status()
            body = resp.json()
            code = str(body.get("code"))
            if code == "0":
                return body
            msg = f"okx 接口错误 code={code} msg={body.get('msg')!r} path={path}"
            if code == _RETRYABLE_CODE and attempt < _MAX_RETRY:
                last = RuntimeError(msg)
                time.sleep(_backoff(attempt))
                continue
            raise RuntimeError(msg)
        raise RuntimeError(f"okx 请求重试耗尽（{_MAX_RETRY} 次）: {path}: {last}")

    # ——— 符号枚举 ———

    def list_symbols(self, refresh: bool = False) -> list[str]:
        """全部 **USDT 结算**永续的 `instId`（如 `BTC-USDT-SWAP`）。实例级缓存。

        过滤 `settleCcy=='USDT'`（对齐币安 USDT-M 口径；币本位 SWAP 不做——两所的计价币种
        一致性是「混合 crypto 持仓可加总」的前提）。
        ⚠️ 待 prod 实测：是否需要再按 `state` 收窄（`preopen`/`suspend` 标的拉不到数据，
        会白耗配额并让 `actual_days` 虚低）。当前**按设计 §4.2 只做 settleCcy 过滤**——
        证据不足时不预先收窄。
        """
        if refresh:
            self._insts = None            # 批 108·步 2：refresh 须让 instruments 缓存**一并失效**
        if self._symbols is not None and not refresh:
            return self._symbols
        self._symbols = sorted(
            str(it.get("instId")) for it in self._instruments()
            if it.get("instId") and str(it.get("settleCcy", "")).upper() == "USDT")
        return self._symbols

    def _instruments(self) -> list[dict]:
        """`instruments?instType=SWAP` 全量 `data`（实例级缓存）——`list_symbols` 与
        `symbol_inception` **共用一次请求**（批 108·步 2）。"""
        if self._insts is None:
            body = self._request(_INSTRUMENTS, {"instType": "SWAP"})
            self._insts = body.get("data") or []
        return self._insts

    def symbol_inception(self, symbol: str) -> str | None:
        """`listTime`（交易所自报**上币时间**，毫秒 epoch 字符串）→ ISO 日期；缺失/畸形 ⇒ `None`。

        批 108·步 2：`listTime` **一直在响应里、此前被丢弃**（`list_symbols` 只取 `instId`）。
        它是**真上币日**，与 `available_range`（源可达下界 `2020-01-01`）**不同义**——
        实测差 ≈2 月（`listTime=2019-11-12` vs K 线可达 `2020-01-01`，迁移 `0137` prod 实测）。
        ⇒ 本值供 **inception**（SM `list_date`），**不得**拿去当源界（裁定 F / §九.3）。
        """
        inst = str(symbol).split(".")[0]
        for it in self._instruments():
            if str(it.get("instId")) == inst:
                ms = it.get("listTime")
                if ms in (None, ""):
                    return None
                try:
                    return datetime.fromtimestamp(
                        int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                except (TypeError, ValueError):
                    return None
        return None

    # ——— 源可达区间（批 108·步 1）———

    def available_range(self, kind: str) -> tuple[str | None, str | None]:
        """见基类契约。取值＝**`2020-01-01`**（迁移 `0137` 的 **prod 实测**：「K 线 API 在
        2019 全空」，故 floor 取实测 K 线边界，**不臆造**）。

        ⚠️ 本条同时是「**可达性 ≠ 上币日**」的实证样本（设计 §2.6/§九.3）：`instruments.listTime`
        报 BTC/ATOM `2019-11-12`、FIL `2019-08-10`，而 K 线 2019 **全空** ⇒ 差 ≈2 月。
        **inception 取 `listTime`、源界取本值**，两者不得互替。
        """
        return (_SOURCE_EARLIEST, None)

    def publish_lag(self, kind: str) -> int:
        """1 自然日（`1Dutc` 日线在 UTC 日界后可用；对齐 `_crypto_window` 的「上界＝UTC 昨日」）。"""
        return 1

    # ——— 拉取 ———

    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "crypto") -> pd.DataFrame:
        """per-symbol 日线（区间含 `end` 当日；`start`/`end` = 'YYYYMMDD'）。

        分页＝`after` **游标向后翻**（OKX 语义：返回**早于**该 ts 的记录），响应按 ts 倒序。
        终止条件三选一：空页 / `len(data) < _LIMIT`（数据到底）/ 已翻到 `start` 之前。

        窗口外的行**在此剔除**（防御：游标页边界必然越过区间端点，不剔就会把区间外的数据
        写进来——`to_bar_rows` 无窗口概念，回补场景下会污染历史）。
        """
        inst = str(symbol).split(".")[0]
        s_d, e_d = _to_date(start), _to_date(end)
        if s_d > e_d:
            return pd.DataFrame()
        start_ms = _ms(s_d)
        end_ms = _ms(e_d + timedelta(days=1))      # 含 end 当日
        frames: list[list] = []
        cursor = end_ms
        for _ in range(_MAX_PAGES):
            body = self._request(_CANDLES, {"instId": inst, "bar": _BAR,
                                            "after": str(cursor), "limit": _LIMIT})
            data = body.get("data") or []
            if not data:
                break
            frames.extend(r for r in data if start_ms <= int(r[0]) < end_ms)
            oldest = min(int(r[0]) for r in data)
            if oldest <= start_ms or len(data) < _LIMIT:
                break
            cursor = oldest
        else:
            logger.warning("okx %s 分页触顶 %d 页仍未到 %s——区间被截断（与 start_floor 一并复核）",
                           inst, _MAX_PAGES, start)
        if not frames:
            return pd.DataFrame()
        df = pd.DataFrame(frames, columns=_CANDLE_COLS).drop_duplicates(subset=["ts"])
        df = df.sort_values("ts").reset_index(drop=True)
        df["okx_symbol"] = inst
        return df

    def pull_minute(self, symbol: str, freq: str, start: str, end: str) -> pd.DataFrame:
        """本批不做盘中 bar（`capabilities` 只含日线）——**响亮拒绝**，不返回空帧。

        空帧会被上游当「该窗口无数据」静默记账（与 binance `pull_minute` 的拒绝理由同源）。
        """
        raise UnsupportedFeature("okx adapter 本批只做日线（okx_perp_daily）；盘中 bar 未实现")

    # ——— 归一化 ———

    def to_bar_rows(self, df: pd.DataFrame, freq: str, adj_map: dict | None = None) -> list[tuple]:
        """→ 统一 11 字段 (symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)。

        - symbol = `<instId>.OKX`（源原生 instId + 后缀，**不跨所归一**）
        - ts = `open_time` → **UTC aware**（naive 会被 `db.validate_bars` 按上海解释，故必须 aware）
        - `volume = volCcy`（**币量**；`vol` 是张数，各标的 ctVal 不同 ⇒ 直接用是垃圾）
        - `amount = volQuote`（计价额）；`adj_factor = None`（加密无复权概念）
        - **只取 `confirm=='1'`**（未收盘 bar 剔除；T+1 拉取天然只取已收盘行，此为兜底）
        """
        rows: list[tuple] = []
        if df is None or df.empty:
            return rows
        for r in df.to_dict("records"):
            inst = str(r.get("okx_symbol") or r.get("symbol") or "")
            if not inst:
                continue
            confirm = str(r.get("confirm", "1"))
            if confirm not in ("1", "True", "true"):      # 缺列按已收盘（历史形态）
                continue
            sym = inst if inst.upper().endswith(_SYMBOL_SUFFIX) else f"{inst}{_SYMBOL_SUFFIX}"
            ts = datetime.fromtimestamp(int(r["ts"]) / 1000, tz=timezone.utc)
            rows.append((
                sym, freq, ts,
                float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                float(r["volCcy"]), float(r["volQuote"]),
                None, self.provider,
            ))
        return rows

    def fetch_supply(self, kind: str, sub_kind: str | None = None, **params) -> pd.DataFrame:
        """批 100 供给端口：`('bar_daily', 'perp')` → per-symbol 区间拉取（源原生 DataFrame）。

        参数：`symbol`（instId，可带 `.OKX`）/ `start` / `end`（'YYYYMMDD'）；`freq` 忽略
        （本源本批只有日线）。与 binance 侧同构 ⇒ 证明批 100 的端口**与 provider 无关**。
        """
        if kind == "bar_daily" and sub_kind in (None, "perp"):
            return self.pull_daily(params.get("symbol", ""),
                                   params.get("start", ""), params.get("end", ""))
        raise UnsupportedFeature(f"okx 未实现 fetch_supply(kind={kind}, sub_kind={sub_kind})")
