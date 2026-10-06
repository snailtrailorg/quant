"""批 102b · OKX 永续日线（第 2 个加密数据源；**多加密市场共存**验证）。

**验证方式的由来**（决定了本文件的形态）：2026-10-06 实测 dev/prod 直连 `www.okx.com`
均不可达（prod 解析到 169.254.0.2＝污染），只有 **prod 经代理出口**可达 ⇒ 真机语义
（`1Dutc` 是否被接受、历史深度、限频实际带宽）**留 prod 实测**。本文件因此全部走
**mock 上游**：把「契约面/映射面/失败可见面」钉死，把「上游事实」交给产线。

验收面（全部钉「行为/契约」，不是「函数存在」）：
1. **三注册表一致性**（`_ADAPTERS` / `data_source._REGISTRY` / `interfaces` + `_PROVIDER_MODULES`）
   ——漏一处＝「清单有·实现无」静默回落 tushare（串源）；
2. **两把键不混**：数据源 `okx`（hist_quote，0 密钥）≠ 交易通道 `okx_perp`（trading/rt_quote）；
3. **契约层**：`bar_daily` 非聚合域 ⇒ `sub_kinds` 必须为空；`ts` 必须 UTC aware；
4. **语义三处（本批最易踩错，设计 §4.3）**：
   - 日界＝`bar=1Dutc`（**UTC 锚定**；OKX 裸 `1D` 是 UTC+8，与币安裸混＝静默错位）；
   - `volume=volCcy`（币量）、`amount=volQuote`——**不是 `vol`**（那是张数，各标的 ctVal 不同）；
   - 符号＝`<instId>.OKX`，**不跨所归一**（`BTC-USDT-SWAP.OKX` 与 `BTCUSDT.BINANCE` 永不互认）；
5. **失败必须可见**：`code != '0'` 响亮抛（**不返回空帧**）；空帧会被上游当「该窗口无数据」
   记 success，把故障埋掉；符号枚举空 → handler 响亮 raise（不推游标）；
6. **重试面三条路**：网络异常 / HTTP 429·5xx（优先 `Retry-After`）/ 业务限频 `code=50011`；
7. **分页契约**：`after` 游标向后翻 + 窗口外行剔除 + `ts` 去重（防回补污染历史）；
8. **handler 与 provider 无关**：同一条 `_make_crypto_bar_handler` 链，游标/回补/失败语义
   与币安完全同构——**泛化回归钉**（币安侧断言仍在 test_batch101/_101b 原位）。
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_platform.adapters import okx_adapter as OA
from src.data_platform.adapters.base import UnsupportedFeature

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _ms(y, m, d) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def _candle(ts_ms: int, o=1.0, h=2.0, lo=0.5, c=1.5, vol=7.0, vol_ccy=100.0,
            vol_quote=150.0, confirm="1") -> list:
    """OKX candles 9 列（实测列序）：[ts,o,h,l,c,vol,volCcy,volQuote,confirm]。"""
    return [str(ts_ms), str(o), str(h), str(lo), str(c),
            str(vol), str(vol_ccy), str(vol_quote), confirm]


def _df(rows, inst="BTC-USDT-SWAP") -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=OA._CANDLE_COLS)
    df["okx_symbol"] = inst
    return df


def _resp(status=200, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {"code": "0", "data": []}
    r.headers = headers or {}
    return r


# ---------------------------------------------------------------------------
# 1：注册面（import 副作用 + 三注册表一致性）
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_adapter_registered_under_okx(self):
        """`__init__.py` 的显式导入是注册的唯一触发点——不导＝静默缺席＝回落 tushare 拉错源。"""
        from src.data_platform.adapters.base import _ADAPTERS, get_adapter
        assert "okx" in _ADAPTERS, "okx_adapter 未被导入 ⇒ 注册表静默缺席"
        assert _ADAPTERS["okx"] is OA.OkxAdapter
        assert get_adapter("okx").provider == "okx"

    def test_three_registries_consistent_for_okx(self):
        """三处各注册一类（批 83b 立法）：漏 DataSource ⇒ get_data_source 抛 ProviderConfigError。"""
        from src.data_platform.data_source import _REGISTRY, OkxDataSource, get_data_source
        from src.data_platform.interfaces import get_interface_provider
        assert _REGISTRY.get("okx") is OkxDataSource, "缺 data_source._REGISTRY 注册＝串源"
        assert get_interface_provider("okx") is not None, "缺 interfaces 注册（或未入 _PROVIDER_MODULES）"
        assert get_data_source("okx") is None or get_data_source("okx").provider == "okx"

    def test_provider_module_is_listed(self):
        """`_PROVIDER_MODULES` 是凭证页签下拉的真源——不在表里＝provider 永远选不到。"""
        from src.data_platform.interfaces import base as iface_base
        assert "okx" in iface_base._PROVIDER_MODULES
        # 交易桩仍不挂（零消费者；挂回由 102 实时腿做）
        assert "okx_perp" not in iface_base._PROVIDER_MODULES

    def test_data_source_needs_no_credentials(self):
        """0 密钥：空 FIELD_SCHEMA ⇒ required_fields 空集 ⇒ 建行不需要任何凭证。"""
        from src.data_platform.interfaces import get_interface_provider
        inst = get_interface_provider("okx")
        assert inst.FIELD_SCHEMA == [] and inst.PARAMS_SCHEMA == []
        assert inst.required_fields == set()
        assert inst.market == "crypto"

    def test_capability_decls_legal_and_non_aggregate(self):
        """`bar_daily` 非聚合域 ⇒ `sub_kinds` 必须为空（contract 硬约束，非法即 import 期 ValueError）。"""
        from src.quant_common.contract import CRYPTO_ALL, validate_capability_decls
        decls = OA.OkxAdapter.capability_decls
        assert decls, "adapter 必须有真声明（否则 register_adapter 钩子空转）"
        assert validate_capability_decls(decls) == []
        d = decls[0]
        assert (d.kind, d.temporality) == ("bar_daily", "historical")
        assert d.scope == CRYPTO_ALL
        assert d.sub_kinds == frozenset(), "bar_daily 非聚合域，sub_kinds 必须为空"

    def test_capabilities_are_sync_id_level_and_day_only(self):
        """本批只做日线：`capabilities` 恰一条，且不含实时/分钟（虚报会让 resolve 路由错）。"""
        caps = OA.OkxAdapter.capabilities
        assert caps == {"okx_perp_daily"}
        assert "rt_quote" not in caps
        assert not any("minute" in c or "hourly" in c for c in caps), \
            "本批不做盘中 bar，虚留 sync_id ＝「清单有·实现无」"


# ---------------------------------------------------------------------------
# 2：两把键不混（okx ≠ okx_perp）
# ---------------------------------------------------------------------------


class TestProviderKeyDisambiguation:
    def test_okx_and_okx_perp_are_distinct_keys(self):
        """数据源面 `okx`（hist_quote）与下单面 `okx_perp`（trading/rt_quote）是两把键。

        混用会让 `_get_supply_adapter` 找不到 adapter 而**静默回落 tushare**（同 binance 族）。
        """
        from src.data_platform.capabilities import provider_capabilities, provider_domain
        from src.quant_common.markets import NON_DATA_PROVIDERS, PROVIDER_MARKET
        assert "okx" not in NON_DATA_PROVIDERS, "okx 是数据源（有 adapter），不是无 adapter 通道"
        assert NON_DATA_PROVIDERS["okx_perp"] == {"trading", "rt_quote"}
        assert provider_capabilities("okx") == {"hist_quote"}
        assert provider_capabilities("okx_perp") == {"trading", "rt_quote"}
        assert PROVIDER_MARKET["okx"] == "crypto" == PROVIDER_MARKET["okx_perp"]
        assert provider_domain("okx") == "data_source"
        assert provider_domain("okx_perp") == "trading_account"

    def test_supply_adapter_resolves_to_okx_not_tushare(self):
        """消费者断言：`provider='okx'` 真路由到 OkxAdapter（不是回落 tushare）。"""
        from src.data_platform.adapters.base import TushareAdapter
        from src.data_sync.engine import _get_supply_adapter
        ad = _get_supply_adapter({"id": "okx_perp_daily", "provider": "okx"})
        assert isinstance(ad, OA.OkxAdapter)
        assert not isinstance(ad, TushareAdapter), "provider='okx' 回落 tushare＝拉错源"

    def test_cap_map_and_provider_market_registered(self):
        """`SYNC_ID_CAP_MAP` / `PROVIDER_MARKET` 双登记（漏登记＝「清单有·能力无」）。"""
        from src.quant_common.markets import CAPABILITIES, PROVIDER_MARKET, SYNC_ID_CAP_MAP
        assert SYNC_ID_CAP_MAP["okx_perp_daily"] == "hist_quote"
        assert SYNC_ID_CAP_MAP["okx_perp_daily"] in CAPABILITIES
        assert PROVIDER_MARKET["okx"] == "crypto"


# ---------------------------------------------------------------------------
# 3：出口配置接线（102a）——OKX 在 prod 可用的前提
# ---------------------------------------------------------------------------


class TestExitConfig:
    def test_supports_exit_config_and_is_proxyable_consumer(self):
        """`supports_exit_config=True` 是「配了代理会不会生效」的唯一真源——
        它同时决定本 adapter 出现在集成中心「代理出口」页的可选消费方里。"""
        from src.data_platform.proxy import capable_consumers
        assert OA.OkxAdapter.supports_exit_config is True
        assert "okx" in capable_consumers()

    def test_exit_injection_and_default_base(self):
        """注入后 `_exit()` 用注入端点；未注入 ⇒ 缺省 `www.okx.com`。"""
        ad = OA.OkxAdapter()
        assert ad.exit_config() == (None, None)
        assert ad._exit() == (None, OA._BASE)
        ad.configure_exit(proxy="socks5h://u:p@h:1080", endpoint=None)
        assert ad._exit() == ("socks5h://u:p@h:1080", OA._BASE)
        ad.configure_exit(proxy="http://h:3128", endpoint="https://mirror.example")
        assert ad._exit() == ("http://h:3128", "https://mirror.example")

    def test_exit_config_is_db_free(self):
        """`configure_exit`/`exit_config` **不触库**（解析在 engine 侧一次性做）。

        反证意义：若在此处解析，任何手搓 adapter 的单测都会连带 dev 库，
        且「每任务解析一次」的语义会被破坏（adapter 内多次解析可漂移）。
        """
        with patch("src.data_platform.db.get_conn", side_effect=AssertionError("不该触库")):
            ad = OA.OkxAdapter()
            ad.configure_exit(proxy="socks5h://h:1080")
            assert ad.exit_config()[0] == "socks5h://h:1080"

    def test_proxy_reaches_requests(self):
        """代理必须真落到 requests 的 `proxies` 上（否则「配了但没生效」＝静默无效配置）。"""
        ad = OA.OkxAdapter()
        ad.configure_exit(proxy="socks5h://h:1080")
        with patch.object(OA.requests, "get", return_value=_resp()) as g, \
             patch.object(OA.time, "sleep"):
            ad._request(OA._INSTRUMENTS, {"instType": "SWAP"})
        assert g.call_args.kwargs["proxies"] == {"http": "socks5h://h:1080",
                                                 "https": "socks5h://h:1080"}

    def test_direct_when_no_proxy(self):
        ad = OA.OkxAdapter()
        with patch.object(OA.requests, "get", return_value=_resp()) as g, \
             patch.object(OA.time, "sleep"):
            ad._request(OA._INSTRUMENTS, {"instType": "SWAP"})
        assert g.call_args.kwargs["proxies"] is None


# ---------------------------------------------------------------------------
# 4：请求层——重试三条路 + 响亮失败（不静默空帧）
# ---------------------------------------------------------------------------


class TestRequestRetry:
    def test_ok_body_returns_immediately(self):
        ad = OA.OkxAdapter()
        with patch.object(OA.requests, "get", return_value=_resp(body={"code": "0", "data": [1]})) as g, \
             patch.object(OA.time, "sleep") as sl:
            body = ad._request("/x", {})
        assert body["data"] == [1]
        assert g.call_count == 1 and not sl.called

    def test_retries_on_429_then_succeeds(self):
        ad = OA.OkxAdapter()
        seq = [_resp(status=429, headers={"Retry-After": "1"}),
               _resp(body={"code": "0", "data": []})]
        with patch.object(OA.requests, "get", side_effect=seq), patch.object(OA.time, "sleep") as sl:
            assert ad._request("/x", {})["code"] == "0"
        assert sl.call_args.args[0] == 1.0, "429 应优先采用 Retry-After"

    def test_retries_on_5xx(self):
        ad = OA.OkxAdapter()
        seq = [_resp(status=503), _resp(body={"code": "0", "data": []})]
        with patch.object(OA.requests, "get", side_effect=seq), patch.object(OA.time, "sleep"):
            assert ad._request("/x", {})["code"] == "0"

    def test_retries_on_network_exception(self):
        import requests as rq
        ad = OA.OkxAdapter()
        seq = [rq.ConnectionError("boom"), _resp(body={"code": "0", "data": []})]
        with patch.object(OA.requests, "get", side_effect=seq), patch.object(OA.time, "sleep"):
            assert ad._request("/x", {})["code"] == "0"

    def test_retries_on_business_rate_limit_code(self):
        """`code=50011`（业务层限频，HTTP 仍 200）**最易漏的一路**——必须重试。"""
        ad = OA.OkxAdapter()
        seq = [_resp(body={"code": "50011", "msg": "too many requests"}),
               _resp(body={"code": "0", "data": []})]
        with patch.object(OA.requests, "get", side_effect=seq), patch.object(OA.time, "sleep") as sl:
            assert ad._request("/x", {})["code"] == "0"
        assert sl.called

    def test_exhausted_retries_raises_not_empty(self):
        """重试耗尽必须**抛**——返回空帧会被上游当「该窗口无数据」记 success，把故障埋掉。"""
        ad = OA.OkxAdapter()
        with patch.object(OA.requests, "get", return_value=_resp(status=503)), \
             patch.object(OA.time, "sleep"):
            with pytest.raises(Exception):
                ad._request("/x", {})

    def test_business_error_raises_loud(self):
        """非限频业务码（如 51001 参数错）**不重试、直接抛**，且消息含 code（可诊断）。"""
        ad = OA.OkxAdapter()
        with patch.object(OA.requests, "get", return_value=_resp(
                body={"code": "51001", "msg": "Instrument ID does not exist"})) as g, \
             patch.object(OA.time, "sleep") as sl:
            with pytest.raises(RuntimeError) as ei:
                ad._request("/x", {})
        assert "51001" in str(ei.value)
        assert g.call_count == 1 and not sl.called, "参数错不该退避重试（浪费配额）"

    def test_throttle_window_is_rolling(self):
        """滚动窗口节流：窗口内 `_RATE_MAX` 次不等待；第 `_RATE_MAX+1` 次必须等（IP 共享额度守门）。

        单调钟此处人为推进（真实调用里它自然前进）——`monotonic` 若恒定，窗口永不过期，
        `_wait_slot` 的 while 循环会**真死循环**（这本身就是该实现的一个前提：单调钟必须前进）。
        """
        ad = OA.OkxAdapter()
        # 4 次调用取 4 个 now；第 4 次超窗 → sleep → 循环回读第 5 个 now（已前进 2.1s）
        ticks = [0.0, 0.0, 0.0, 0.0, 2.1]
        with patch.object(OA, "_RATE_MAX", 3), \
             patch.object(OA.time, "monotonic", side_effect=ticks), \
             patch.object(OA.time, "sleep") as sl:
            ad._wait_slot()
            ad._wait_slot()
            ad._wait_slot()
            assert not sl.called, "窗口内（3 次）不该被节流"
            ad._wait_slot()
        assert sl.call_count == 1, "第 4 次超窗 ⇒ 必须等待一次"
        assert sl.call_args.args[0] == pytest.approx(OA._RATE_WINDOW)


# ---------------------------------------------------------------------------
# 5：符号枚举（instruments 接口；settleCcy=USDT 过滤）
# ---------------------------------------------------------------------------


class TestListSymbols:
    def test_filters_usdt_settled_and_caches(self):
        """只留 `settleCcy=='USDT'`（与币安 USDT-M 同口径）——币本位 SWAP 混入会让
        「混合 crypto 持仓可加总」的前提失效。进程内缓存（每轮重复枚举＝白耗 IP 配额）。"""
        ad = OA.OkxAdapter()
        body = {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "settleCcy": "USDT"},
            {"instId": "ETH-USDT-SWAP", "settleCcy": "USDT"},
            {"instId": "BTC-USD-SWAP", "settleCcy": "BTC"},      # 币本位 → 剔
            {"instId": "SOL-USDC-SWAP", "settleCcy": "USDC"},    # 非 USDT → 剔
        ]}
        with patch.object(OA.OkxAdapter, "_request", return_value=body) as m:
            syms = ad.list_symbols()
        assert syms == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
        assert m.call_args.args[1] == {"instType": "SWAP"}       # 通道正确
        with patch.object(OA.OkxAdapter, "_request",
                          side_effect=AssertionError("不应再打网络")):
            assert ad.list_symbols() == syms, "缓存未生效"

    def test_refresh_forces_refetch(self):
        ad = OA.OkxAdapter()
        with patch.object(OA.OkxAdapter, "_request",
                          return_value={"code": "0", "data": [{"instId": "BTC-USDT-SWAP",
                                                              "settleCcy": "USDT"}]}) as m:
            ad.list_symbols()
            ad.list_symbols(refresh=True)
        assert m.call_count == 2

    def test_empty_instruments_is_not_silently_ok(self):
        """枚举空必须让上游（handler）能察觉——本层返回 []，由 handler 响亮 raise。"""
        ad = OA.OkxAdapter()
        with patch.object(OA.OkxAdapter, "_request", return_value={"code": "0", "data": []}):
            assert ad.list_symbols() == []


# ---------------------------------------------------------------------------
# 6：分页拉取（after 游标 / 窗口裁剪 / 去重）
# ---------------------------------------------------------------------------


class TestPullDaily:
    def _pages(self, pages):
        return patch.object(OA.OkxAdapter, "_request", side_effect=pages)

    def test_bar_is_utc_anchored(self):
        """🔴 语义①：必须 `1Dutc`（UTC 日界）。裸 `1D` 是 UTC+8——与币安裸混＝静默错位。"""
        ad = OA.OkxAdapter()
        page = {"code": "0", "data": [_candle(_ms(2024, 1, 2))]}
        with self._pages([page]) as m:
            ad.pull_daily("BTC-USDT-SWAP", "20240101", "20240105")
        assert m.call_args.args[1]["bar"] == "1Dutc"
        assert OA._BAR == "1Dutc"

    def test_instid_suffix_stripped_for_request(self):
        """调用方可给带/不带 `.OKX` 的符号——源侧请求必须用裸 instId。"""
        ad = OA.OkxAdapter()
        with self._pages([{"code": "0", "data": [_candle(_ms(2024, 1, 2))]}]) as m:
            ad.pull_daily("BTC-USDT-SWAP.OKX", "20240101", "20240105")
        assert m.call_args.args[1]["instId"] == "BTC-USDT-SWAP"

    def test_paginates_via_after_and_trims_window(self):
        """`after` 游标向后翻 + 窗口外行剔除 + `ts` 去重（回补场景不剔会污染历史）。"""
        ad = OA.OkxAdapter()
        p1 = {"code": "0", "data": [_candle(_ms(2024, 1, 6)), _candle(_ms(2024, 1, 5))]}
        p2 = {"code": "0", "data": [_candle(_ms(2024, 1, 5)), _candle(_ms(2024, 1, 4))]}
        p3 = {"code": "0", "data": [_candle(_ms(2024, 1, 3)), _candle(_ms(2024, 1, 2))]}
        p4 = {"code": "0", "data": [_candle(_ms(2024, 1, 1)), _candle(_ms(2023, 12, 31))]}
        with patch.object(OA, "_LIMIT", 2), patch.object(OA, "_MAX_PAGES", 10), \
             patch.object(OA.OkxAdapter, "_request", side_effect=[p1, p2, p3, p4]) as m:
            df = ad.pull_daily("BTC-USDT-SWAP", "20240102", "20240105")
        got = [datetime.fromtimestamp(int(t) / 1000, tz=timezone.utc).date()
               for t in df["ts"].tolist()]
        # 窗口 [01-02, 01-05]：01-06 与 2023-12-31 被剔；01-05 重复项去重
        assert got == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
        # 第 1 次的 after = end_ms；其后每次 = 上一页最小值
        assert m.call_args_list[0].args[1]["after"] == str(_ms(2024, 1, 6))
        assert m.call_args_list[1].args[1]["after"] == str(_ms(2024, 1, 5))
        assert m.call_count == 3, "第 3 页起 oldest <= start 已终止，不该继续翻"

    def test_short_page_terminates(self):
        """`len(data) < limit` ＝ 数据到底 ⇒ 立即止（不做无谓的下一页）。"""
        ad = OA.OkxAdapter()
        with patch.object(OA.OkxAdapter, "_request",
                          return_value={"code": "0", "data": [_candle(_ms(2024, 1, 2))]}) as m:
            ad.pull_daily("BTC-USDT-SWAP", "20240101", "20240105")
        assert m.call_count == 1

    def test_start_after_end_returns_empty_without_request(self):
        ad = OA.OkxAdapter()
        with patch.object(OA.OkxAdapter, "_request",
                          side_effect=AssertionError("窗口压空不该发请求")):
            df = ad.pull_daily("BTC-USDT-SWAP", "20240105", "20240101")
        assert df.empty

    def test_pagination_cap_warns(self):
        """触顶 `_MAX_PAGES` 必须告警（区间被截断是**静默数据洞**的高发形态）。"""
        ad = OA.OkxAdapter()
        full = {"code": "0", "data": [_candle(_ms(2024, 1, 5)), _candle(_ms(2024, 1, 4))]}
        with patch.object(OA, "_LIMIT", 2), patch.object(OA, "_MAX_PAGES", 2), \
             patch.object(OA.OkxAdapter, "_request", return_value=full), \
             patch.object(OA.logger, "warning") as w:
            ad.pull_daily("BTC-USDT-SWAP", "20200101", "20240105")
        assert any("分页触顶" in str(c.args[0]) for c in w.call_args_list), \
            "触顶被静默吞掉＝区间截断不可见"


# ---------------------------------------------------------------------------
# 7：归一化（语义②③ + 11 字段契约）
# ---------------------------------------------------------------------------


class TestToBarRows:
    def test_contract_11_fields_and_volume_mapping(self):
        """🔴 语义②：`volume=volCcy`（币量）、`amount=volQuote`——**不是 `vol`**（张数）。"""
        ad = OA.OkxAdapter()
        rows = ad.to_bar_rows(_df([_candle(_ms(2024, 1, 2), vol=7.0, vol_ccy=100.0,
                                          vol_quote=150.0)]), "1D")
        sym, freq, ts, o, h, lo, c, vol, amt, adj, src = rows[0]
        assert sym == "BTC-USDT-SWAP.OKX"
        assert freq == "1D"
        assert (o, h, lo, c) == (1.0, 2.0, 0.5, 1.5)
        assert (vol, amt) == (100.0, 150.0), "volume 必须是 volCcy（币量），不是 vol（张数）"
        assert adj is None, "加密无复权概念"
        assert src == "okx"
        assert isinstance(ts, datetime) and ts.utcoffset() == timedelta(0), "ts 必须 UTC aware"
        assert ts == datetime(2024, 1, 2, tzinfo=timezone.utc)

    def test_unconfirmed_bars_dropped(self):
        """只取 `confirm=='1'`（未收盘 bar 剔除；T+1 拉取天然已收盘，此为兜底）。"""
        ad = OA.OkxAdapter()
        rows = ad.to_bar_rows(_df([_candle(_ms(2024, 1, 2), confirm="0"),
                                   _candle(_ms(2024, 1, 3), confirm="1")]), "1D")
        assert len(rows) == 1
        assert rows[0][2] == datetime(2024, 1, 3, tzinfo=timezone.utc)

    def test_missing_confirm_treated_as_closed(self):
        """历史形态可能无 confirm 列——按已收盘处理（否则整段历史被静默丢掉）。"""
        ad = OA.OkxAdapter()
        df = _df([_candle(_ms(2024, 1, 2))])
        df = df.drop(columns=["confirm"])
        assert len(ad.to_bar_rows(df, "1D")) == 1

    def test_no_cross_exchange_normalization(self):
        """🔴 语义③：`.OKX` 后缀**不与 `.BINANCE` 互认**——两个不同标的，永不归一。"""
        ad = OA.OkxAdapter()
        rows = ad.to_bar_rows(_df([_candle(_ms(2024, 1, 2))], inst="BTC-USDT-SWAP"), "1D")
        assert rows[0][0] == "BTC-USDT-SWAP.OKX"
        assert "BINANCE" not in rows[0][0]
        # 已带后缀不重复加
        rows2 = ad.to_bar_rows(_df([_candle(_ms(2024, 1, 2))], inst="BTC-USDT-SWAP.OKX"), "1D")
        assert rows2[0][0] == "BTC-USDT-SWAP.OKX"

    def test_rows_survive_write_gate(self):
        from src.data_platform.db import validate_bars
        rows = OA.OkxAdapter().to_bar_rows(_df([_candle(_ms(2024, 1, 2))]), "1D")
        assert len(validate_bars(list(rows))) == len(rows)

    def test_symbol_convention_agrees_with_markets_layer(self):
        """`.OKX` → crypto（markets 与 security_master 双单源一致），且与 `.BINANCE` 同市场。"""
        from src.data_platform.security_master import SMClient
        from src.quant_common.markets import market_of_symbol
        assert market_of_symbol("BTC-USDT-SWAP.OKX") == "crypto"
        assert market_of_symbol("BTCUSDT.BINANCE") == "crypto"
        assert SMClient._market_of_suffix("OKX") == "crypto"

    def test_empty_df(self):
        assert OA.OkxAdapter().to_bar_rows(pd.DataFrame(), "1D") == []


# ---------------------------------------------------------------------------
# 8：供给端口分派（批 100 端口 → 与 provider 无关）
# ---------------------------------------------------------------------------


class TestFetchSupply:
    def test_dispatch_bar_daily_perp(self):
        ad = OA.OkxAdapter()
        with patch.object(OA.OkxAdapter, "pull_daily", return_value=pd.DataFrame()) as m:
            ad.fetch_supply("bar_daily", "perp", symbol="BTC-USDT-SWAP",
                            start="20240101", end="20240105")
        assert m.call_args.args == ("BTC-USDT-SWAP", "20240101", "20240105")

    def test_dispatch_accepts_none_sub_kind(self):
        ad = OA.OkxAdapter()
        with patch.object(OA.OkxAdapter, "pull_daily", return_value=pd.DataFrame()) as m:
            ad.fetch_supply("bar_daily", None, symbol="BTC-USDT-SWAP",
                            start="20240101", end="20240102")
        assert m.called

    def test_out_of_domain_kind_raises_loud(self):
        ad = OA.OkxAdapter()
        with pytest.raises(UnsupportedFeature):
            ad.fetch_supply("bar_daily", "stock")
        with pytest.raises(UnsupportedFeature):
            ad.fetch_supply("bar_minute", "perp")

    def test_pull_minute_raises_loud(self):
        """本批不做盘中 bar——**响亮拒绝**，不返回空帧（空帧会被上游静默记账）。"""
        with pytest.raises(UnsupportedFeature):
            OA.OkxAdapter().pull_minute("BTC-USDT-SWAP", "1min", "20240101", "20240105")


# ---------------------------------------------------------------------------
# 9：handler（同一条 crypto bar 链；游标/回补/失败可见）
# ---------------------------------------------------------------------------


class _FakeAdapter:
    provider = "okx"

    def __init__(self, symbols=("BTC-USDT-SWAP", "ETH-USDT-SWAP"), empty=False):
        self._symbols = list(symbols)
        self._empty = empty

    def list_symbols(self, refresh=False):
        return list(self._symbols)

    def fetch_supply(self, kind, sub_kind=None, **params):
        assert (kind, sub_kind) == ("bar_daily", "perp")
        if self._empty:
            return pd.DataFrame()
        return pd.DataFrame([{"okx_symbol": params["symbol"], "ts": _ms(2024, 1, 2)}])

    def to_bar_rows(self, df, freq, adj_map=None):
        return [(f"{df['okx_symbol'].iloc[0]}.OKX", freq,
                 datetime(2024, 1, 2, tzinfo=timezone.utc),
                 1.0, 2.0, 0.5, 1.5, 100.0, 150.0, None, "okx")]


class TestHandler:
    def _run(self, adapter, cfg=None, backfill=None):
        from src.data_sync import engine
        cfg = cfg or {"id": "okx_perp_daily", "provider": "okx", "last_sync_date": "20240101"}
        with patch.object(engine, "_get_supply_adapter", return_value=adapter), \
             patch("src.data_platform.db.save_bars", return_value=2) as sb, \
             patch("src.data_platform.db.save_bars_overwrite", return_value=2) as so:
            r = engine._sync_okx_perp_daily(cfg, "20240131", backfill)
        return r, sb, so

    def test_registered_and_uses_supply_adapter(self):
        """注册 + provider 真路由（不能硬编码 adapter 类）。"""
        from src.data_sync import engine
        assert engine._HANDLERS["okx_perp_daily"] is engine._sync_okx_perp_daily
        assert "_get_supply_adapter(" in inspect.getsource(engine._sync_okx_perp_daily)
        assert "okx_perp_daily" not in engine._VIA_KIND_IDS, \
            "crypto 无交易日历，误入 _VIA_KIND_IDS 会走 A 股心智的逐日批路径"

    def test_writes_rows_and_cursor_is_last_data_day(self):
        r, sb, so = self._run(_FakeAdapter())
        assert r["pulled"] == 2 and r["saved"] == 4
        assert sb.call_count == 2 and so.call_count == 0
        assert r["failed_dates"] == []
        assert r["cursor_upto"] == "20240102", "游标必须落在真取到数据的那一日（非窗口上界）"
        assert r["start"] == "20240102"

    def test_backfill_uses_overwrite(self):
        r, sb, so = self._run(_FakeAdapter(), backfill="20240101")
        assert so.call_count == 2 and sb.call_count == 0

    def test_empty_symbol_list_raises_loud_with_okx_label(self):
        from src.data_sync import engine
        with patch.object(engine, "_get_supply_adapter", return_value=_FakeAdapter(symbols=())):
            with pytest.raises(RuntimeError) as ei:
                engine._sync_okx_perp_daily({"id": "okx_perp_daily", "provider": "okx"},
                                           "20240131", None)
        assert "okx" in str(ei.value).lower(), "错误消息必须点明源名（多源共存后是必需的可诊断性）"

    def test_all_window_zero_rows_is_visible(self):
        r, _sb, _so = self._run(_FakeAdapter(empty=True))
        assert r["pulled"] == 0
        assert any(x.startswith("no_rows:") for x in r["failed_dates"])

    def test_start_after_end_is_noop(self):
        from src.data_sync import engine
        with patch.object(engine, "_get_supply_adapter", return_value=_FakeAdapter()):
            r = engine._sync_okx_perp_daily(
                {"id": "okx_perp_daily", "provider": "okx"}, "20240101", "20240105")
        assert r["pulled"] == 0 and r["saved"] == 0 and r["cursor_upto"] == "20240101"

    def test_shared_factory_still_names_both_handlers(self):
        """泛化回归钉：两个 handler 由同一工厂产出、`__name__` 各自正确
        （币安侧的行为断言仍在 test_batch101/_101b 原位，此处只钉泛化未伤及命名）。"""
        from src.data_sync import engine
        assert engine._sync_okx_perp_daily.__name__ == "_sync_okx_perp_daily"
        assert engine._sync_binance_perp_daily.__name__ == "_sync_binance_perp_daily"
        assert engine._sync_okx_perp_daily is not engine._sync_binance_perp_daily


# ---------------------------------------------------------------------------
# 10：迁移 0136 结构（source 级；不打库）
# ---------------------------------------------------------------------------


class TestMigrationShape:
    def _mod(self):
        import importlib
        return importlib.import_module("migrations.versions.0136_okx_perp_daily")

    def test_revision_chain(self):
        m = self._mod()
        assert m.revision == "0136" and m.down_revision == "0135"

    def test_0136_is_followed_by_0137(self):
        """head 交棒钉：0136 已不再是 head，而是被 **0137**（start_floor 证据化回填）接续。

        原 `test_is_chain_head`（`heads == {"0136"}`）在批 102b 补新增 0137 后按设计**交棒**
        ——head 钉恒挂在链尾、挪而不删，现由 `TestMigration0137Shape.test_is_chain_head` 承载。
        """
        import importlib
        import pathlib
        revs = {}
        base = pathlib.Path("migrations/versions")
        for p in base.glob("0*.py"):
            mod = importlib.import_module(f"migrations.versions.{p.stem}")
            revs[mod.revision] = str(mod.down_revision)
        assert revs.get("0137") == "0136", f"0137 未接在 0136 之后：{revs.get('0137')}"

    def test_expand_only_no_ddl(self):
        """expand-only：upgrade 零 DDL（阶段 4 破坏性门不拦；回滚只回代码）。"""
        src = inspect.getsource(self._mod().upgrade).upper()
        for bad in ("ALTER TABLE", "DROP ", "TRUNCATE", "RENAME"):
            assert bad not in src, f"upgrade 含破坏性语句 {bad}"

    def test_idempotent_conflict_guards(self):
        src = inspect.getsource(self._mod().upgrade)
        assert src.count("ON CONFLICT") >= 2

    def test_downgrade_symmetric(self):
        src = inspect.getsource(self._mod().downgrade)
        assert "DELETE FROM sync_kind_config" in src and "DELETE FROM sync_config" in src

    def test_seed_values(self):
        src = inspect.getsource(self._mod().upgrade)
        for tok in ("okx.candles", "bar_1D", "crypto", "none", "okx",
                    "bar_daily", "perp", "bar_1d", "40 8 * * *"):
            assert tok in src, f"迁移缺少 {tok}"

    def test_schedule_staggered_from_binance(self):
        """🔴 调度错峰是**设计裁定**（§4.2），不是随手的分钟数：两个 T+1 任务共享 worker
        池/代理出口 ⇒ 同时刻起跑会自相排队。币安 `30 8`，OKX `40 8`。"""
        src = inspect.getsource(self._mod().upgrade)
        assert "40 8 * * *" in src and "30 8 * * *" not in src

    def test_start_floor_deliberately_absent(self):
        """`start_floor` 必须**不在 0136 的插入列清单里**（当时最早可得日未实证，禁臆造下界）。

        值本身**不臆造**：0136 留 NULL ⇒ 由 **0137** 在 prod 实测后回填（OKX `history-candles`
        的保留边界 = `2020-01-01`；**不用 `instruments.listTime`**——它对 BTC/ATOM 给 2019-11-12、
        对 FIL 给 2019-08-10，而 K 线 API 在 2019 全空）。故本钉只保证「0136 不写它」。
        """
        import re
        src = inspect.getsource(self._mod().upgrade)
        m = re.search(r"INSERT INTO sync_config\s*\(([^)]*)\)", src)
        assert m, "未找到 sync_config 的 INSERT 列清单"
        cols = {c.strip() for c in m.group(1).split(",")}
        assert "start_floor" not in cols, "未实证的下界不得写入（臆造 start_floor）"
        assert "supports_backfill" in cols and "trade_day_filter" in cols


# ---------------------------------------------------------------------------
# 11：真库对账（无 dev 库自动跳过）
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
        rows = self._q("SELECT provider, trade_day_filter, supports_backfill, start_floor, "
                       "data_type, enabled, sync_mode, schedule FROM sync_config "
                       "WHERE id='okx_perp_daily'")
        assert len(rows) == 1, "迁移 0136 未跑（okx_perp_daily 行缺失）"
        provider, tdf, sbf, floor, dtype, enabled, mode, sched = rows[0]
        assert (provider, tdf, dtype) == ("okx", "none", "crypto")
        assert sbf is True
        assert str(floor) == "2020-01-01", (
            "0137 已按 prod 实测回填 start_floor（OKX K 线保留边界；非 listTime=2019-11-12）")
        assert enabled is True and mode == "incremental"
        assert sched == "40 8 * * *", "须与币安 30 8 错峰（设计 §4.2）"

    def test_sync_kind_config_row(self):
        rows = self._q("SELECT kind, sub_kind, pg_table, rebuild FROM sync_kind_config "
                       "WHERE sync_id='okx_perp_daily'")
        assert rows == [("bar_daily", "perp", "bar_1d", "incremental")]

    def test_capabilities_cover_own_sync_ids(self):
        """`okx` adapter 的 capabilities 必须覆盖它名下全部 sync_config 行。"""
        own = {r[0] for r in self._q("SELECT id FROM sync_config WHERE provider='okx'")}
        assert own <= set(OA.OkxAdapter.capabilities), (
            f"okx 能力矩阵漏声明：{sorted(own - set(OA.OkxAdapter.capabilities))}")

    def test_two_crypto_sources_coexist_in_one_table(self):
        """多加密市场共存的结构前提：`.OKX` 与 `.BINANCE` 同表同 freq（`bar_1d`）。"""
        rows = self._q("SELECT sync_id, pg_table FROM sync_kind_config "
                       "WHERE sync_id IN ('okx_perp_daily','binance_perp_daily')")
        assert dict(rows) == {"okx_perp_daily": "bar_1d", "binance_perp_daily": "bar_1d"}


# ---------------------------------------------------------------------------
# 13：迁移 0137 结构（source 级；不打库）——start_floor 证据化回填
# ---------------------------------------------------------------------------


class TestMigration0137Shape:
    def _mod(self):
        import importlib
        return importlib.import_module("migrations.versions.0137_okx_start_floor")

    def test_revision_chain(self):
        m = self._mod()
        assert m.revision == "0137" and m.down_revision == "0136"

    def test_is_chain_head(self):
        """0137 是当前 head（链尾钉；后续批再往后接时本钉会红——照此挪，勿删）。"""
        import importlib
        import pathlib
        revs = {}
        base = pathlib.Path("migrations/versions")
        for p in base.glob("0*.py"):
            mod = importlib.import_module(f"migrations.versions.{p.stem}")
            revs[mod.revision] = str(mod.down_revision)
        heads = set(revs) - set(revs.values())
        assert heads == {"0137"}, f"head 不是唯一 0137：{sorted(heads)}"

    def test_expand_only_no_ddl(self):
        """expand-only：upgrade 零 DDL（阶段 4 破坏性门不拦；回滚只回代码）。"""
        src = inspect.getsource(self._mod().upgrade).upper()
        for bad in ("ALTER TABLE", "DROP ", "TRUNCATE", "RENAME", "DELETE "):
            assert bad not in src, f"upgrade 含破坏性语句 {bad}"

    def test_upgrade_value_is_measured_not_listtime(self):
        """🔴 回填值必须是**实测的 K 线保留边界** `2020-01-01`。

        反面：`instruments.listTime` 给 BTC/ATOM `2019-11-12`、FIL `2019-08-10`，但 K 线 API
        在 2019 **全空**——拿 listTime 当 floor 会让回补白烧限频额度（20req/2s 共享出口）。
        """
        src = inspect.getsource(self._mod().upgrade)
        assert "2020-01-01" in src, "值应为 prod 实测的 K 线保留边界"
        assert "2019" not in src, "不得用 listTime（2019-xx）当数据下界"

    def test_upgrade_does_not_clobber_manual_edit(self):
        """带 `IS NULL` 守卫：只填未声明行 ⇒ 运维若已手填，本迁移让位。"""
        src = inspect.getsource(self._mod().upgrade)
        assert "start_floor IS NULL" in src, "须加 IS NULL 守卫（不覆盖运维手改）"

    def test_downgrade_symmetric_and_guarded(self):
        """降级只回收**本迁移所设**的值（现值被手改成别的则不动）。"""
        src = inspect.getsource(self._mod().downgrade)
        assert "SET start_floor = NULL" in src
        assert "2020-01-01" in src, "仅当现值仍为本迁移所设值才回收（不覆盖手改）"
