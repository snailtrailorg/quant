"""批 101 · 加密数据层第一步：币安 USDⓈ-M 永续日线落 `bar_1d`。

**背景**：原判「外部 gate＝币安/OKX API 未开通」已被 prod 实测推翻（2026-10-06）——公开行情
端点本就无鉴权，真 gate 是**网络**：`data.binance.vision`（官方批量 ZIP 站，含 USDT-M 永续）
在大陆 prod 直连可达（T+1），而 `fapi.binance.com`（永续实时）/`www.okx.com` 全阻断。
故本批＝**非实时（历史 + T+1）零 key 零代理**先落地；实时腿待境外 relay（批 102）。

验收面（全部钉「行为/契约」，不是「函数存在」）：
1. **三注册表一致性**（`_ADAPTERS` / `data_source._REGISTRY` / `interfaces._REGISTRY`）——
   漏一处＝串源（`test_provider_registry` 的既有构建期闸的批 101 落点）；
2. **两把键不混**：数据源 `binance`（hist_quote，0 密钥）≠ 交易通道 `binance_perp`（trading/rt_quote）；
3. **契约层**：`bar_daily` 非聚合域 ⇒ `sub_kinds` 必须为空；`ts` 必须 UTC aware（否则 +8h 静默错位）；
4. **符号枚举**（fapi 被墙时唯一权威通道＝S3 ListObjectsV2）：分页 + 排除 `_YYMMDD` 交割合约 +
   非 ASCII 符号 URL 编码；
5. **月包压缩**：整月走 monthly（≥2020-01）、其余 daily —— 回补请求数从「全 daily」压到 ~1/30；
6. **T+1 窗口上界＝UTC 昨日**：否则每轮把「尚未落盘的今天」记成失败，游标永不动；
7. **handler 失败必须可见**：符号枚举空 → 响亮 raise（不静默 success）；全窗 0 行 → 进 failed；
8. **死构件真退役**：原 `data-increment-crypto` beat（每 15min 唤醒、恒 return skipped）+
   `tasks.data_increment_crypto` 已删——回归即红。
"""
from __future__ import annotations

import inspect
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_platform.adapters import binance_adapter as BC


# ---------------------------------------------------------------------------
# 工具：内存 ZIP / XML / 行构造
# ---------------------------------------------------------------------------

def _ms(y, m, d, hh=0) -> int:
    return int(datetime(y, m, d, hh, tzinfo=timezone.utc).timestamp() * 1000)


def _row(open_time: int, o=1.0, h=2.0, lo=0.5, c=1.5, vol=100.0, close_time=None, qv=150.0):
    """12 列 K 线 CSV 行（实测列序）。"""
    return [open_time, o, h, lo, c, vol, close_time if close_time is not None else open_time + 86_399_999,
            qv, 42, 60.0, 90.0, 0]


def _zip(rows, header: bool = True, name: str = "BTCUSDT-1d-2024-01-02.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        lines = ([",".join(BC._COLS)] if header else []) + \
                [",".join(str(x) for x in r) for r in rows]
        z.writestr(name, "\n".join(lines) + "\n")
    return buf.getvalue()


def _s3_page(symbols, token: str | None = None) -> bytes:
    """S3 ListObjectsV2 响应（delimiter 形态：CommonPrefixes）。"""
    prefix = "".join(
        f"<CommonPrefixes><Prefix>data/futures/um/monthly/klines/{s}/</Prefix></CommonPrefixes>"
        for s in symbols)
    nxt = f"<NextContinuationToken>{token}</NextContinuationToken>" if token else ""
    return f"<ListBucketResult>{prefix}{nxt}</ListBucketResult>".encode()


# ---------------------------------------------------------------------------
# 1：注册面（import 副作用 + 三注册表一致性）
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_adapter_registered_under_binance(self):
        """`__init__.py` 的显式导入是注册的唯一触发点——不导＝静默缺席＝回落 tushare 拉错源。"""
        from src.data_platform.adapters.base import _ADAPTERS, get_adapter
        assert "binance" in _ADAPTERS, "binance_adapter 未被导入 ⇒ 注册表静默缺席"
        assert _ADAPTERS["binance"] is BC.BinanceAdapter
        assert get_adapter("binance").provider == "binance"

    def test_three_registries_consistent_for_binance(self):
        """三处各注册一类（批 83b立法）：漏 DataSource ⇒ get_data_source 抛 ProviderConfigError。"""
        from src.data_platform.data_source import _REGISTRY, get_data_source
        from src.data_platform.interfaces import get_interface_provider
        assert _REGISTRY.get("binance") is not None, "缺 data_source._REGISTRY 注册＝串源"
        assert get_interface_provider("binance") is not None, "缺 interfaces 注册"
        # 无 DB 行 → None（合法路径，非抛错；抛错只发生在「有 adapter 无 DataSource」）
        assert get_data_source("binance") is None or get_data_source("binance").provider == "binance"

    def test_data_source_needs_no_credentials(self):
        """0 密钥：空 FIELD_SCHEMA ⇒ required_fields 空集 ⇒ 建行不需要任何凭证。"""
        from src.data_platform.interfaces import get_interface_provider
        inst = get_interface_provider("binance")
        assert inst.FIELD_SCHEMA == [] and inst.PARAMS_SCHEMA == []
        assert inst.required_fields == set()
        assert inst.market == "crypto"

    def test_capability_decls_legal_and_non_aggregate(self):
        """`bar_daily` 非聚合域 ⇒ `sub_kinds` 必须为空（contract 硬约束，非法即 import 期 ValueError）。

        反证意义：日后有人「顺手」给 bar_daily 加 sub_kinds={'perp'}，装饰器钩子会直接拒绝注册。
        """
        from src.quant_common.contract import CRYPTO_ALL, validate_capability_decls
        decls = BC.BinanceAdapter.capability_decls
        assert decls, "adapter 必须有真声明（否则 register_adapter 钩子空转）"
        assert validate_capability_decls(decls) == []
        d = decls[0]
        assert (d.kind, d.temporality) == ("bar_daily", "historical")
        assert d.scope == CRYPTO_ALL
        assert d.sub_kinds == frozenset(), "bar_daily 非聚合域，sub_kinds 必须为空"

    def test_capabilities_are_sync_id_level(self):
        """`capabilities`=sync_id 集合（供 `_validate_provider` 查表），不含 trading/rt_quote。"""
        caps = BC.BinanceAdapter.capabilities
        assert caps == {"binance_perp_daily"}
        assert "rt_quote" not in caps, "批量站是 T+1 静态文件，虚报实时＝把实盘决策路由到无实时能力的源"


# ---------------------------------------------------------------------------
# 2：两把键不混（binance ≠ binance_perp）
# ---------------------------------------------------------------------------

class TestProviderKeyDisambiguation:
    def test_binance_and_binance_perp_are_distinct_keys(self):
        """数据源面 `binance`（hist_quote）与下单面 `binance_perp`（trading/rt_quote）是两把键。

        混用会让 `_get_supply_adapter` 找不到 adapter 而**静默回落 tushare**。
        """
        from src.data_platform.capabilities import provider_capabilities, provider_domain
        from src.quant_common.markets import NON_DATA_PROVIDERS, PROVIDER_MARKET
        assert "binance" not in NON_DATA_PROVIDERS, "binance 是数据源（有 adapter），不是无 adapter 通道"
        assert NON_DATA_PROVIDERS["binance_perp"] == {"trading", "rt_quote"}
        assert provider_capabilities("binance") == {"hist_quote"}
        assert provider_capabilities("binance_perp") == {"trading", "rt_quote"}
        # 两者同市场不同域
        assert PROVIDER_MARKET["binance"] == "crypto" == PROVIDER_MARKET["binance_perp"]
        assert provider_domain("binance") == "data_source"
        assert provider_domain("binance_perp") == "trading_account"

    def test_supply_adapter_resolves_to_binance_not_tushare(self):
        """消费者断言：`provider='binance'` 真路由到 BinanceAdapter（不是回落 tushare）。

        这是「清单有·实现无」那一族的直接防线——若注册缺失，`get_adapter` 抛 ValueError 后
        引擎会静默回落 tushare 并用币安的名字去拉 A 股（或直接拉空）。
        """
        from src.data_sync.engine import _get_supply_adapter
        from src.data_platform.adapters.base import TushareAdapter
        ad = _get_supply_adapter({"id": "binance_perp_daily", "provider": "binance"})
        assert isinstance(ad, BC.BinanceAdapter)
        assert not isinstance(ad, TushareAdapter), "provider='binance' 回落 tushare＝拉错源"

    def test_cap_map_and_provider_market_registered(self):
        """`SYNC_ID_CAP_MAP` / `PROVIDER_MARKET` 双登记（漏登记＝「清单有·能力无」）。"""
        from src.quant_common.markets import CAPABILITIES, PROVIDER_MARKET, SYNC_ID_CAP_MAP
        assert SYNC_ID_CAP_MAP["binance_perp_daily"] == "hist_quote"
        assert SYNC_ID_CAP_MAP["binance_perp_daily"] in CAPABILITIES
        assert PROVIDER_MARKET["binance"] == "crypto"


# ---------------------------------------------------------------------------
# 3：符号枚举（S3 ListObjectsV2——fapi 被墙时唯一权威通道）
# ---------------------------------------------------------------------------

class TestListSymbols:
    def test_paginates_and_excludes_delivery_contracts(self):
        """分页取全 + 排除 `_YYMMDD` 交割合约 + 进程内缓存。"""
        ad = BC.BinanceAdapter()
        pages = [
            _s3_page(["BTCUSDT", "ETHUSDT", "BTCUSDT_210129"], token="TOK2"),   # 第 1 页带续期 token
            _s3_page(["ETHUSDT_210326", "SOLUSDT"]),                            # 第 2 页无 token → 止
        ]
        calls = []

        def _fake_get(url, timeout=BC._TIMEOUT):
            calls.append(url)
            return pages[len(calls) - 1]

        with patch.object(BC, "_get", new=_fake_get):
            syms = ad.list_symbols()
        assert syms == ["BTCUSDT", "ETHUSDT", "SOLUSDT"], syms      # 两个交割合约被排除
        assert len(calls) == 2, "未跟随 NextContinuationToken 分页"
        assert "continuation-token=TOK2" in calls[1], "第 2 页未带续期 token"
        with patch.object(BC, "_get", new=MagicMock(side_effect=AssertionError("不应再打网络"))):
            assert ad.list_symbols() == syms, "缓存未生效（每轮重复枚举＝白耗）"

    def test_kline_url_encodes_non_ascii_symbol(self):
        """实测存在中文名符号（如 `币安人生USDT`）——不编码则 URL 非法、下载必失败。"""
        url = BC._kline_url("币安人生USDT", "1d", "daily", "2024-01-02")
        assert url.startswith("https://data.binance.vision/data/futures/um/daily/klines/")
        assert "%E5%B8%81" in url and "币" not in url
        assert url.endswith("%E5%B8%81%E5%AE%89%E4%BA%BA%E7%94%9FUSDT-1d-2024-01-02.zip")


# ---------------------------------------------------------------------------
# 4：区间 → (period, key) 映射（月包压缩）
# ---------------------------------------------------------------------------

class TestPeriodKeys:
    def test_full_months_use_monthly(self):
        keys = BC._period_keys(date(2020, 1, 1), date(2020, 3, 31))
        assert keys == [("monthly", "2020-01"), ("monthly", "2020-02"), ("monthly", "2020-03")]

    def test_compression_ratio(self):
        """6 整年 ⇒ 84 个月包、0 个日包（对「全 daily」= ~2190 请求的 1/26）。

        反证意义：回归成「逐日」会让回补请求数暴涨 26 倍（且更易被 CDN 限流）。
        """
        keys = BC._period_keys(date(2020, 1, 1), date(2026, 12, 31))
        assert sum(1 for p, _ in keys if p == "monthly") == 84
        assert all(p == "monthly" for p, _ in keys)

    def test_partial_month_falls_back_to_daily(self):
        """不完整月不能整月拉（月包只在月结束后才存在）——逐日补齐。"""
        keys = BC._period_keys(date(2020, 1, 15), date(2020, 2, 10))
        assert all(p == "daily" for p, _ in keys)
        assert len(keys) == (31 - 15 + 1) + 10          # 1/15..1/31 + 2/1..2/10

    def test_before_monthly_floor_uses_daily(self):
        """实测月包自 2020-01 起（2019-09/10 → 404）⇒ 更早区间必须回退日包。"""
        keys = BC._period_keys(date(2019, 9, 8), date(2019, 9, 30))
        assert all(p == "daily" for p, _ in keys)
        assert len(keys) == 23                          # 9/8..9/30
        # 跨过 2020-01 边界：严格按 floor 分界
        keys2 = BC._period_keys(date(2019, 12, 1), date(2020, 1, 31))
        assert ("monthly", "2019-12") not in keys2
        assert ("monthly", "2020-01") in keys2


class TestPullDailySavingsValve:
    """省流阀（>90 天先探最早月包）的两个方向都不能错。"""

    def _urls(self, probe, start="20190908", sym="BTCUSDT"):
        ad = BC.BinanceAdapter()
        seen: list[str] = []

        def _fake_get(url, timeout=BC._TIMEOUT):
            seen.append(url)
            return None

        with patch.object(BC, "_get", new=_fake_get), \
             patch.object(BC.BinanceAdapter, "first_available_month", return_value=probe):
            ad.pull_daily(sym, start, "20240101")
        return seen

    def test_pre_floor_history_not_silently_cut(self):
        """反证：起点早于月包 floor 时**不得**把起点抬到 floor。

        否则 2019-09~2019-12 的逐日历史静默消失，而 config 声明的
        `start_floor='2019-09-08'` 就变成谎言（静默缺口＝本仓 P0 病类）。
        """
        urls = self._urls(probe=(2020, 1))
        assert any("BTCUSDT-1d-2019-09-08.zip" in u for u in urls), "pre-floor 日包被裁掉"
        assert any("/daily/" in u and "BTCUSDT-1d-2019-12-31.zip" in u for u in urls)
        assert any("/monthly/" in u and "BTCUSDT-1d-2020-01.zip" in u for u in urls), "月包未接管 2020+"

    def test_late_listed_symbol_clamped_to_probe(self):
        """上线晚的符号（月包起点晚于 floor）必须被上抬——否则白打几年 404（1004 标的 × 几十次）。"""
        urls = self._urls(probe=(2023, 5), sym="NEWUSDT")
        assert not any("2019-" in u for u in urls), "晚期上市符号仍在打 2019 日包 404"
        assert any("/monthly/" in u and "NEWUSDT-1d-2023-05.zip" in u for u in urls)
        assert sum(1 for u in urls if "/daily/" in u) == 1, "仅当月尾一天走日包（不完整月）"

    def test_short_span_skips_probe(self):
        """<=90 天区间不探测（省一次网络往返；增量语义本就短窗）。"""
        ad = BC.BinanceAdapter()
        with patch.object(BC.BinanceAdapter, "first_available_month",
                          side_effect=AssertionError("短窗不该探测")) as m, \
             patch.object(BC, "_get", return_value=None):
            ad.pull_daily("BTCUSDT", "20240101", "20240131")
        assert not m.called


# ---------------------------------------------------------------------------
# 5：解析（ZIP → DataFrame；有无表头两形态）
# ---------------------------------------------------------------------------
class TestParseKlineCsv:
    def test_with_header(self):
        blob = _zip([_row(_ms(2024, 1, 2)), _row(_ms(2024, 1, 3), o=2, h=3, lo=1, c=2.5)])
        df = BC._parse_kline_csv(blob)
        assert list(df.columns) == BC._COLS
        assert len(df) == 2
        assert df["open"].tolist() == [1.0, 2.0]
        assert df["quote_volume"].tolist() == [150.0, 150.0]
        assert str(df["open_time"].dtype) == "Int64"

    def test_without_header(self):
        """旧文件无表头（实测两形态并存）——探测首格 == 'open_time' 决定是否弃首行。"""
        blob = _zip([_row(_ms(2024, 1, 2))], header=False)
        df = BC._parse_kline_csv(blob)
        assert len(df) == 1 and df["close"].iloc[0] == 1.5

    def test_only_unparseable_rows_dropped(self):
        """不可解析行（NaN open_time）在此剔；`ohlc=0` 坏行**留给写入门** `db.validate_bars`。

        实测过：两处都剔＝口径双份，日后必漂移（故 parser 不越界）。
        """
        from src.data_platform.db import validate_bars
        rows = [_row(_ms(2024, 1, 2)), _row(_ms(2024, 1, 3), o=0, h=0, lo=0, c=0),
                ["not-a-number", 1, 2, 0.5, 1.5, 1, 1, 1, 1, 1, 1, 0]]
        df = BC._parse_kline_csv(_zip(rows))
        assert len(df) == 2, "NaN open_time 行未被剔"
        df["binance_symbol"] = "BTCUSDT"
        rows_out = BC.BinanceAdapter().to_bar_rows(df, "1D")
        assert len(validate_bars(rows_out)) == 1, "ohlc=0 坏行应在写入门被剔"

    def test_get_returns_none_on_404_only(self):
        """404 = 正常态（未上市/未到账/停牌）；其它 HTTP 错必须抛（不吞）。"""
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 404, "nf", {}, None)):
            assert BC._get("http://x") is None
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 500, "err", {}, None)):
            with pytest.raises(urllib.error.HTTPError):
                BC._get("http://x")


# ---------------------------------------------------------------------------
# 6：to_bar_rows（11 字段契约；ts 必须 UTC aware）
# ---------------------------------------------------------------------------

class TestToBarRows:
    def _df(self):
        ad = BC.BinanceAdapter()
        with patch.object(BC, "_get", return_value=_zip([
                _row(_ms(2024, 1, 2)), _row(_ms(2024, 1, 3), o=2, h=3, lo=1, c=2.5)])):
            with patch.object(BC.BinanceAdapter, "first_available_month", return_value=(2024, 1)):
                return ad.pull_daily("BTCUSDT", "20240101", "20240105")

    def test_contract_11_fields(self):
        rows = BC.BinanceAdapter().to_bar_rows(self._df(), "1D")
        assert len(rows) == 2
        sym, freq, ts, o, h, lo, c, vol, amt, adj, src = rows[0]
        assert sym == "BTCUSDT.BINANCE"                 # vt_symbol 形态（security_master 认）
        assert freq == "1D"
        assert (o, h, lo, c) == (1.0, 2.0, 0.5, 1.5)
        assert (vol, amt) == (100.0, 150.0)             # volume=base 数量；amount=quote_volume
        assert adj is None                              # 加密无复权概念
        assert src == "binance"
        assert isinstance(ts, datetime) and ts.tzinfo is not None
        assert ts.utcoffset() == timedelta(0), "ts 必须 UTC aware"
        assert ts == datetime(2024, 1, 2, tzinfo=timezone.utc), "ts 取 open_time（bar 起始）"

    def test_dedup_and_sort(self):
        rows = BC.BinanceAdapter().to_bar_rows(self._df(), "1D")
        ts_list = [r[2] for r in rows]
        assert ts_list == sorted(ts_list)

    def test_naive_ts_would_be_8h_off(self):
        """反证：naive ts 进 `db.as_utc` 会被按上海解释 —— 同一字面量语义差 8h（静默错位）。

        这就是 `to_bar_rows` **必须**用 `tz=timezone.utc` 构造的原因（不是风格问题）。
        """
        from src.data_platform.tz import as_utc
        naive = datetime(2024, 1, 2, 0, 0)
        aware = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
        assert as_utc(naive) != as_utc(aware)
        assert as_utc(aware) == aware

    def test_rows_survive_write_gate(self):
        """行必须能过 `db.validate_bars`（ohlc>0 ⇒ 零剔除）——否则「拉了但写不进」。"""
        from src.data_platform.db import validate_bars
        rows = BC.BinanceAdapter().to_bar_rows(self._df(), "1D")
        assert len(validate_bars(list(rows))) == len(rows)

    def test_already_suffixed_symbol_not_doubled(self):
        ad = BC.BinanceAdapter()
        df = pd.DataFrame([dict(zip(BC._COLS, _row(_ms(2024, 1, 2))))])
        df["binance_symbol"] = "BTCUSDT.BINANCE"
        rows = ad.to_bar_rows(df, "1D")
        assert rows[0][0] == "BTCUSDT.BINANCE"

    def test_freq_passthrough(self):
        rows = BC.BinanceAdapter().to_bar_rows(self._df(), "1d")
        assert all(r[1] == "1d" for r in rows)


# ---------------------------------------------------------------------------
# 7：fetch_supply 端口分派（批 100 端口 → 与 provider 无关）
# ---------------------------------------------------------------------------

class TestFetchSupply:
    def test_dispatch_bar_daily_perp(self):
        ad = BC.BinanceAdapter()
        with patch.object(BC.BinanceAdapter, "pull_daily",
                          return_value=pd.DataFrame()) as m:
            ad.fetch_supply("bar_daily", "perp", symbol="BTCUSDT", start="20240101", end="20240105")
        assert m.call_args.args == ("BTCUSDT", "20240101", "20240105")

    def test_dispatch_accepts_none_sub_kind(self):
        ad = BC.BinanceAdapter()
        with patch.object(BC.BinanceAdapter, "pull_daily", return_value=pd.DataFrame()) as m:
            ad.fetch_supply("bar_daily", None, symbol="BTCUSDT", start="20240101", end="20240102")
        assert m.called

    def test_unknown_kind_raises_loud(self):
        from src.data_platform.adapters.base import UnsupportedFeature
        ad = BC.BinanceAdapter()
        with pytest.raises(UnsupportedFeature):
            ad.fetch_supply("bar_daily", "stock")          # 非本端口域
        with pytest.raises(UnsupportedFeature):
            ad.fetch_supply("bar_minute", "perp")          # 分钟线留批 101b

    def test_pull_minute_marker(self):
        with pytest.raises(NotImplementedError):
            BC.BinanceAdapter().pull_minute("BTCUSDT", "1h", "20240101", "20240105")


# ---------------------------------------------------------------------------
# 8：T+1 窗口（_crypto_window）
# ---------------------------------------------------------------------------

class TestCryptoWindow:
    def test_end_is_utc_yesterday(self):
        """上界＝UTC 昨日（实测：UTC 当日文件 404、前一日 200）。

        反证意义：上界若取 end_date（＝今天），每轮把「未落盘的今天」记成失败日，
        `sync()` 因 failed_dates 走 partial ⇒ 游标只推到「连续成功末日」…最终卡在 1 天前，
        且每天刷一条失败告警。
        """
        from src.data_sync.engine import _crypto_window
        utc_today = datetime.now(timezone.utc).date()
        _s, e = _crypto_window({"last_sync_date": None}, utc_today.strftime("%Y%m%d"), None)
        assert e == utc_today - timedelta(days=1)
        assert e < utc_today, "上界绝不可取「今天」（UTC 当日文件尚未落盘）"

    def test_backfill_from_sets_start(self):
        from src.data_sync.engine import _crypto_window
        s, e = _crypto_window({"last_sync_date": "20240101"}, "20240131", "20190908")
        assert s == date(2019, 9, 8)
        assert e == min(date(2024, 1, 31), date.today() - timedelta(days=1))

    def test_incremental_start_is_cursor_plus_one(self):
        from src.data_sync.engine import _crypto_window
        s, _e = _crypto_window({"last_sync_date": "20240101"}, "20240131", None)
        assert s == date(2024, 1, 2)

    def test_first_run_30_day_window(self):
        from src.data_sync.engine import _crypto_window
        s, e = _crypto_window({"last_sync_date": None}, "20240131", None)
        assert s == e - timedelta(days=30)


# ---------------------------------------------------------------------------
# 9：handler（写入 + 游标上界 + 失败可见）
# ---------------------------------------------------------------------------

class _FakeAdapter:
    provider = "binance"

    def __init__(self, symbols=("BTCUSDT", "ETHUSDT"), rows_per_symbol=1, empty=False):
        self._symbols = list(symbols)
        self._rows = rows_per_symbol
        self._empty = empty

    def list_symbols(self, refresh=False):
        return list(self._symbols)

    def fetch_supply(self, kind, sub_kind=None, **params):
        assert (kind, sub_kind) == ("bar_daily", "perp")
        if self._empty:
            return pd.DataFrame()
        return pd.DataFrame([{"binance_symbol": params["symbol"], "open_time": _ms(2024, 1, 2)}])

    def to_bar_rows(self, df, freq, adj_map=None):
        out = []
        for _ in range(self._rows):
            out.append(("BTCUSDT.BINANCE", freq, datetime(2024, 1, 2, tzinfo=timezone.utc),
                        1.0, 2.0, 0.5, 1.5, 100.0, 150.0, None, "binance"))
        return out


class TestHandler:
    def _run(self, adapter, cfg=None, backfill=None):
        from src.data_sync import engine
        cfg = cfg or {"id": "binance_perp_daily", "provider": "binance", "last_sync_date": "20240101"}
        with patch.object(engine, "_get_supply_adapter", return_value=adapter), \
             patch("src.data_platform.db.save_bars", return_value=2) as sb, \
             patch("src.data_platform.db.save_bars_overwrite", return_value=2) as so:
            r = engine._sync_binance_perp_daily(cfg, "20240131", backfill)
        return r, sb, so

    def test_writes_rows_and_returns_cursor_upto(self):
        r, sb, so = self._run(_FakeAdapter(symbols=("BTCUSDT", "ETHUSDT")))
        assert r["pulled"] == 2, "2 符号 × 各 1 行（to_bar_rows 产出）"
        assert r["saved"] == 4, "save_bars 被调 2 次、每次 mock 返回 2"
        assert sb.call_count == 2 and so.call_count == 0     # 增量走 save_bars
        assert r["failed_dates"] == []
        assert r["cursor_upto"] == "20240131", "游标上界＝窗口上界（≤ end_date，绝不推到今天）"
        assert r["start"] == "20240102"                       # last_sync_date 20240101 + 1

    def test_backfill_uses_overwrite(self):
        r, sb, so = self._run(_FakeAdapter(), backfill="20240101")
        assert so.call_count == 2 and sb.call_count == 0, "回补必须 overwrite（手动优先级最高）"

    def test_empty_symbol_list_raises_loud(self):
        """符号枚举失败必须响亮——静默返回 0 行会让 sync() 记 success 并推进游标（掩埋数据洞）。"""
        from src.data_sync import engine
        with patch.object(engine, "_get_supply_adapter", return_value=_FakeAdapter(symbols=())):
            with pytest.raises(RuntimeError):
                engine._sync_binance_perp_daily({"id": "binance_perp_daily", "provider": "binance"},
                                               "20240131", None)

    def test_all_window_zero_rows_is_visible(self):
        """全窗 0 行＝异常态（窗口内每个在市合约都该有数据）——必须进 failed（→ status=partial）。"""
        r, _sb, _so = self._run(_FakeAdapter(empty=True))
        assert r["pulled"] == 0
        assert any(x.startswith("no_rows:") for x in r["failed_dates"]), r["failed_dates"]

    def test_start_after_end_is_noop(self):
        """窗口压空（如回补起点晚于 T+1 上界）＝显式 0 行，不抛、不写。"""
        from src.data_sync import engine
        with patch.object(engine, "_get_supply_adapter", return_value=_FakeAdapter()):
            r = engine._sync_binance_perp_daily(
                {"id": "binance_perp_daily", "provider": "binance"}, "20240101", "20240105")
        assert r["pulled"] == 0 and r["saved"] == 0 and r["cursor_upto"] == "20240101"


# ---------------------------------------------------------------------------
# 10：调度接线（死构件真退役）
# ---------------------------------------------------------------------------

class TestSchedulerWiring:
    def test_handler_registered(self):
        from src.data_sync import engine
        assert "binance_perp_daily" in engine._HANDLERS
        assert engine._HANDLERS["binance_perp_daily"] is engine._sync_binance_perp_daily

    def test_crypto_not_in_via_kind_ids(self):
        """bar 族静态路由（逐日 _sync_via_kind）不适用于 crypto（无交易日历、按标的×窗口）——
        误入会让它走 A 股心智的逐日批路径。"""
        from src.data_sync import engine
        assert "binance_perp_daily" not in engine._VIA_KIND_IDS

    def test_beat_has_no_dead_crypto_increment(self):
        """反证：原 beat 条目（每 15min 唤醒一个恒 return skipped 的 task）已退役——加回即红。"""
        from src.scheduler.app import app as capp
        beats = capp.conf.beat_schedule or {}
        assert "data-increment-crypto" not in beats
        dead = [k for k, v in beats.items()
                if "data_increment_crypto" in str(v.get("task", ""))]
        assert not dead, f"死构件 beat 回潮：{dead}"

    def test_dead_celery_task_removed(self):
        """原 task（`return {'status':'skipped'}`）已删——存在即死构件回潮。"""
        from src.scheduler import tasks
        assert not hasattr(tasks, "data_increment_crypto")

    def test_strategy_docstring_corrects_the_api_gate_misjudgment(self):
        """策略文档必须点明「原判＝误判 + 真阻碍＝网络」，否则下一个人继续等不存在的门。"""
        from src.strategies import crypto_cta_trend as s
        doc = s.__doc__ or ""
        assert "批 101" in doc
        assert "误判" in doc and "网络" in doc
        assert "fapi.binance.com" in doc and "批 102" in doc


# ---------------------------------------------------------------------------
# 11：能力映射完备性（消费者反证）
# ---------------------------------------------------------------------------

class TestCapabilityCompleteness:
    def test_completeness_predicate_detects_missing_registration(self):
        """反证：从 `SYNC_ID_CAP_MAP` 拿掉 crypto 后，完备性判据必须报缺。

        复刻 `test_batch99_...::test_cap_map_covers_every_sync_id` 的判据（真库侧门）——
        把「漏登记即红」这件事在本文件内做成可证的谓词。
        """
        from src.quant_common.markets import SYNC_ID_CAP_MAP
        declared = {"astock_daily", "binance_perp_daily"}          # 代码侧声明（_HANDLERS ∪ _VIA_KIND_IDS）
        assert declared <= set(SYNC_ID_CAP_MAP)                    # 现状：全覆盖
        broken = {k: v for k, v in SYNC_ID_CAP_MAP.items() if k != "binance_perp_daily"}
        assert declared - set(broken) == {"binance_perp_daily"}, "判据无法发现漏登记"

    def test_crypto_symbol_convention_agrees_with_data_layer(self):
        """数据层写的 `.BINANCE` 后缀 → crypto 市场（markets 与 security_master 双单源一致）。"""
        from src.data_platform.security_master import SMClient
        from src.quant_common.markets import market_of_symbol
        sym = f"BTCUSDT.{BC._VENUE}"
        assert market_of_symbol(sym) == "crypto"
        assert SMClient._market_of_suffix(BC._VENUE) == "crypto"

    def test_strategy_registered_under_crypto_perp(self):
        """策略可被框架取到（防止「数据拉了但策略未注册」）。"""
        from src.strategy_framework.strategy import _STRATEGY_REGISTRY
        from src.strategies.crypto_cta_trend import CryptoCTATrendStrategy
        assert _STRATEGY_REGISTRY.get("crypto_perp") is CryptoCTATrendStrategy


# ---------------------------------------------------------------------------
# 12：迁移 0132 结构（source 级；不打库）
# ---------------------------------------------------------------------------

class TestMigrationShape:
    def _mod(self):
        import importlib
        return importlib.import_module("migrations.versions.0132_binance_perp_daily")

    def test_revision_chain(self):
        m = self._mod()
        assert m.revision == "0132" and m.down_revision == "0131"

    def test_expand_only_no_ddl(self):
        """expand-only：upgrade 零 DDL（阶段 4 破坏性门不拦；回滚只回代码）。"""
        src = inspect.getsource(self._mod().upgrade).upper()
        for bad in ("ALTER TABLE", "DROP ", "TRUNCATE", "RENAME"):
            assert bad not in src, f"upgrade 含破坏性语句 {bad}"

    def test_idempotent_conflict_guards(self):
        """复跑安全（部署中断重跑 / 运维已改行）——两 INSERT 都必须 ON CONFLICT DO NOTHING。"""
        src = inspect.getsource(self._mod().upgrade)
        assert src.count("ON CONFLICT") >= 2

    def test_downgrade_symmetric(self):
        src = inspect.getsource(self._mod().downgrade)
        assert "DELETE FROM sync_kind_config" in src and "DELETE FROM sync_config" in src

    def test_seed_values(self):
        src = inspect.getsource(self._mod().upgrade)
        for tok in ("binance.klines", "bar_1D", "crypto", "none", "binance",
                    "2019-09-08", "bar_daily", "perp", "bar_1d"):
            assert tok in src, f"迁移缺少 {tok}"


# ---------------------------------------------------------------------------
# 13：真库对账（无 dev 库自动跳过）
# ---------------------------------------------------------------------------

def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestRealDb:
    def _q(self, sql, args=()):
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            return conn.execute(sql, args).fetchall()

    def test_sync_config_row(self):
        rows = self._q("SELECT provider, trade_day_filter, supports_backfill, start_floor::text, "
                       "data_type, enabled, sync_mode FROM sync_config WHERE id='binance_perp_daily'")
        assert len(rows) == 1, "迁移 0132 未跑（binance_perp_daily 行缺失）"
        provider, tdf, sbf, floor, dtype, enabled, mode = rows[0]
        assert (provider, tdf, dtype) == ("binance", "none", "crypto")
        assert sbf is True and floor == "2019-09-08"
        assert enabled is True and mode == "incremental"

    def test_sync_kind_config_row(self):
        rows = self._q("SELECT kind, sub_kind, pg_table, rebuild FROM sync_kind_config "
                       "WHERE sync_id='binance_perp_daily'")
        assert rows == [("bar_daily", "perp", "bar_1d", "incremental")]

    def test_capabilities_cover_own_sync_ids(self):
        """`binance` adapter 的 capabilities 必须覆盖它名下全部 sync_config 行。"""
        from src.data_platform.adapters import binance_adapter as bc
        own = {r[0] for r in self._q("SELECT id FROM sync_config WHERE provider='binance'")}
        assert own <= set(bc.BinanceAdapter.capabilities), (
            f"binance 能力矩阵漏声明：{sorted(own - set(bc.BinanceAdapter.capabilities))}")
