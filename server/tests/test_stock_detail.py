"""三档详情页后端单测（项 13/14/15/17）：腾讯行情解析 + 聚合降级链 + 端点。

mock 网络与 Valkey，不连真实服务（隔离完备性教训见 test_pool_data_incremental）。
"""
import json
import pytest
from unittest.mock import patch, MagicMock

# 腾讯行情样例（~ 分隔，GBK 解码后）
_SAMPLE = ('v_sh600000="1~浦发银行~600000~9.00~9.08~9.03~388068~184459~203609~'
           '9.00~3975~8.99~6741~8.98~15660~8.97~11323~8.96~7393~'
           '9.01~710~9.02~429~9.03~544~9.04~1747~9.05~2289~~20260820120444~'
           '-0.08~-0.88~9.13~8.98~9.00/388068/350661544~388068~35066~0.12~5.85~~'
           '9.13~8.98~1.65~2997.53~2997.53~0.40~9.99~8.17~1.33~39373";')


def _fake_redis(data: dict | None = None):
    """极简 Valkey 替身：get/set/expire/delete 够用。"""
    store = data if data is not None else {}

    class _R:
        def get(self, k):
            return store.get(k)

        def set(self, k, v, ex=None):
            store[k] = v
            return True

        def delete(self, *keys):
            for k in keys:
                store.pop(k, None)

    r = _R()
    return r, store


# ── 项 13：腾讯行情 ──

class TestMarketSnapshot:
    def test_tencent_sym(self):
        from src.data_platform.market_snapshot import _tencent_sym
        assert _tencent_sym("600000.SH") == "sh600000"
        assert _tencent_sym("000001.SZ") == "sz000001"
        assert _tencent_sym("830001.BJ") == "bj830001"

    def test_get_quote_parse(self):
        from src.data_platform import market_snapshot as ms
        r, _ = _fake_redis()
        resp = MagicMock(status_code=200)
        resp.content = _SAMPLE.encode("gbk")
        resp.raise_for_status = lambda: None
        with patch.object(ms, "_r", return_value=r), \
             patch("requests.get", return_value=resp):
            q = ms.get_quote("600000.SH")
        assert q is not None
        assert q["name"] == "浦发银行" and q["last"] == 9.0
        assert q["pct_chg"] == -0.88 and q["upper_limit"] == 9.99 and q["lower_limit"] == 8.17
        assert q["bid"] == [9.00, 8.99, 8.98, 8.97, 8.96]
        assert q["ask"] == [9.01, 9.02, 9.03, 9.04, 9.05]
        assert q["source"] == "tencent"

    def test_get_quote_source_fail_returns_none(self):
        from src.data_platform import market_snapshot as ms
        r, _ = _fake_redis()
        with patch.object(ms, "_r", return_value=r), \
             patch("requests.get", side_effect=ConnectionError("down")):
            assert ms.get_quote("600000.SH") is None

    def test_get_quote_malformed_returns_none(self):
        """O 审 B1/B8：畸形响应（北交 pv_none_match/字段不足）安全降级 None。"""
        from src.data_platform import market_snapshot as ms
        r, _ = _fake_redis()
        resp = MagicMock(status_code=200)
        resp.content = 'v_pv_none_match="1";'.encode("gbk")
        resp.raise_for_status = lambda: None
        with patch.object(ms, "_r", return_value=r), \
             patch("requests.get", return_value=resp):
            assert ms.get_quote("830001.BJ") is None

    def test_get_quote_cache_hit(self):
        from src.data_platform import market_snapshot as ms
        r, store = _fake_redis({ms.QUOTE_KEY_PREFIX + "600000.SH": json.dumps({"last": 1.0})})
        called = []
        with patch.object(ms, "_r", return_value=r), \
             patch("requests.get", side_effect=lambda *a, **k: called.append(1)):
            q = ms.get_quote("600000.SH")
        assert q == {"last": 1.0} and not called


# ── 项 14/17：聚合与缓存 ──

class TestStockDetail:
    def test_normalize(self):
        from src.data_platform.stock_detail import _normalize
        assert _normalize("600000.SH") == ("600000.SH", "600000.SHSE")
        assert _normalize("600000.SHSE") == ("600000.SH", "600000.SHSE")

    def test_quote_fallback_hub_to_tencent(self):
        from src.data_platform import stock_detail as sd
        r, store = _fake_redis({"hub:latest_tick:600000.SHSE": json.dumps({"last": 9.0})})
        with patch.object(sd, "_r", return_value=r):
            q = sd._quote_block("600000.SH", "600000.SHSE")
        assert q["source"] == "hub" and q["last"] == 9.0
        # hub miss → tencent（_quote_block 函数内 from .market_snapshot import——patch 源模块）
        r2, _ = _fake_redis()
        from src.data_platform import market_snapshot as ms
        with patch.object(sd, "_r", return_value=r2), \
             patch.object(ms, "get_quote", return_value={"last": 1.0, "source": "tencent"}) as mg:
            q2 = sd._quote_block("600000.SH", "600000.SHSE")
        assert q2["source"] == "tencent" and mg.called

    def test_slow_block_partial_not_cached(self):
        """部分降级块不落缓存（否则缺块 10min）——2026-08-20 本地实测坑。"""
        from src.data_platform import stock_detail as sd
        r, store = _fake_redis()
        full = {k: None for k in sd._CACHEABLE_KEYS}
        with patch.object(sd, "_r", return_value=r), \
             patch.object(sd, "_build_slow", return_value=full):
            sd._slow_block("600000.SH")
        assert store, "完整块应缓存"
        r2, store2 = _fake_redis()
        partial = {k: None for k in sd._CACHEABLE_KEYS - {"finance"}}
        with patch.object(sd, "_r", return_value=r2), \
             patch.object(sd, "_build_slow", return_value=partial):
            sd._slow_block("600000.SH")
        assert not store2, "不完整块不应缓存"

    def test_detail_json_serializable(self):
        """整体可序列化（Decimal/date 曾炸缓存写）——allow_nan=False 对齐 starlette（O 审 S2）。"""
        from src.data_platform import stock_detail as sd
        r, _ = _fake_redis()
        block = {k: None for k in sd._CACHEABLE_KEYS}
        block.update({"name": "测试", "in_pool": False, "moneyflow": [],
                      "events": [{"type": "pledge", "date": "20260801", "pledge_count": 3.0}],
                      "name_changes": []})
        with patch.object(sd, "_r", return_value=r), \
             patch.object(sd, "_build_slow", return_value=block), \
             patch.object(sd, "_quote_block", return_value=None):
            d = sd.get_stock_detail("600000.SH")
        json.dumps(d, allow_nan=False)   # 不抛即过

    def test_ff_blocks_nan(self):
        """O 审 S2：pandas 缺值 NaN 必须归 None（直通 JSON 500+负缓存固化）。"""
        from src.data_platform.stock_detail import _ff
        assert _ff(float("nan")) is None
        assert _ff(None) is None
        assert _ff("1.5") == 1.5
        assert _ff("abc") is None


# ── 项 12：hub latest_tick（O 审 S1——闭包形态曾让字段名错误零覆盖藏身，提模块级后可测）──

class TestHubLatestTick:
    def _make_tick(self, last=9.0):
        from vnpy.trader.object import TickData
        from vnpy.trader.constant import Exchange
        from datetime import datetime
        t = TickData(gateway_name="XTP", symbol="600000", exchange=Exchange.SSE,
                     datetime=datetime(2026, 8, 20, 12, 0, 0))
        t.name = "浦发银行"
        t.last_price, t.open_price, t.high_price, t.low_price = last, 9.0, 9.1, 8.9
        t.pre_close = 9.08
        t.limit_up, t.limit_down = 9.99, 8.17          # vnpy 实名（S1：曾误写 upper_limit）
        t.volume, t.turnover = 388068.0, 350661544.0
        t.bid_price_1, t.bid_volume_1 = 9.00, 3975
        t.ask_price_1, t.ask_volume_1 = 9.01, 710
        t.bid_price_2 = t.bid_price_3 = t.bid_price_4 = t.bid_price_5 = 8.99
        t.bid_volume_2 = t.bid_volume_3 = t.bid_volume_4 = t.bid_volume_5 = 100
        t.ask_price_2 = t.ask_price_3 = t.ask_price_4 = t.ask_price_5 = 9.02
        t.ask_volume_2 = t.ask_volume_3 = t.ask_volume_4 = t.ask_volume_5 = 100
        return t

    def test_writes_tick_with_limit_fields(self):
        from src.md_hub.main import _write_latest_tick, LATEST_TICK_PREFIX
        store = {}
        r = MagicMock()
        r.set.side_effect = lambda k, v, ex=None: store.__setitem__(k, (v, ex))
        fail_ts: dict = {}
        _write_latest_tick(r, "600000.SHSE", self._make_tick(), fail_ts)
        assert LATEST_TICK_PREFIX + "600000.SHSE" in store
        payload, ex = store[LATEST_TICK_PREFIX + "600000.SHSE"]
        d = json.loads(payload)
        assert d["upper_limit"] == 9.99 and d["lower_limit"] == 8.17   # S1 防回归
        assert d["bid"][0] == 9.00 and d["ask"][0] == 9.01
        assert ex == 65

    def test_zero_price_filtered(self):
        """M1：竞价 0 价不上屏。"""
        from src.md_hub.main import _write_latest_tick
        r = MagicMock()
        _write_latest_tick(r, "600000.SHSE", self._make_tick(last=0), {})
        r.set.assert_not_called()

    def test_failure_backoff(self):
        """M6：写失败后 60s 退避窗口内不再撞 Valkey。"""
        from src.md_hub.main import _write_latest_tick
        r = MagicMock()
        r.set.side_effect = ConnectionError("valkey down")
        fail_ts: dict = {}
        _write_latest_tick(r, "600000.SHSE", self._make_tick(), fail_ts)
        _write_latest_tick(r, "600000.SHSE", self._make_tick(), fail_ts)
        assert r.set.call_count == 1          # 第二次被退避拦下
        assert "600000.SHSE" in fail_ts


# ── 端点（薄壳）──

class TestStockApiEndpoints:
    @pytest.fixture
    def admin_client(self):
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        from src.web_api import auth as _auth
        with patch.object(_auth, "verify_jwt",
                          return_value={"sub": "1", "username": "admin", "role": "admin"}):
            client = TestClient(app)
            client.headers.update({"Authorization": "Bearer t"})
            yield client

    def test_search_requires_q(self, admin_client):
        assert admin_client.get("/api/stock/search", params={"q": ""}).status_code == 400

    def test_search_returns_static_symbols(self, admin_client):
        """本地 DB 真查（static_symbols 5539 行）——代码前缀与名称模糊两路。"""
        r = admin_client.get("/api/stock/search", params={"q": "600000"})
        assert r.status_code == 200
        rows = r.json()
        assert any(x["ts_code"] == "600000.SH" for x in rows)
        assert all("symbol" in x and "name" in x for x in rows)

    def test_detail_endpoint_thin_shell(self, admin_client):
        from src.data_platform import stock_detail as sd
        fake = {"symbol": "600000.SHSE", "ts_code": "600000.SH", "name": "浦发银行", "quote": None}
        with patch.object(sd, "get_stock_detail", return_value=fake) as m:
            r = admin_client.get("/api/stock/600000.SH/detail")
        assert r.status_code == 200 and r.json() == fake and m.called
        # B4：未识别标的 404（与 analyze 口径一致）
        with patch.object(sd, "get_stock_detail",
                          return_value={"symbol": "x", "ts_code": "x", "quote": None, "name": None}):
            assert admin_client.get("/api/stock/XXXXXX.XX/detail").status_code == 404

    def test_analyze_caches_and_calls_llm(self, admin_client):
        from src.data_platform import stock_detail as sd
        from src.llm_gateway import gateway as gw
        r, _ = _fake_redis()
        fake_detail = {"ts_code": "600519.SH", "name": "贵州茅台", "industry": "白酒",
                       "in_pool": False, "quote": {"last": 1291.0, "name": "贵州茅台"},
                       "limit": None, "moneyflow": [], "events": [], "name_changes": [],
                       "chips": None, "finance": None, "symbol": "600519.SHSE"}
        fake_resp = MagicMock(content="结构化分析文本")
        with patch.object(sd, "_r", return_value=r), \
             patch.object(sd, "get_stock_detail", return_value=fake_detail), \
             patch.object(gw, "chat", return_value=fake_resp) as mc, \
             patch("src.web_api.main.audit_log") as ma:   # B7：防真写 audit_log
            r1 = admin_client.post("/api/stock/600519.SH/analyze")
            r2 = admin_client.post("/api/stock/600519.SHSE/analyze")   # M3：异型 symbol 同 key
        assert r1.status_code == 200 and r1.json()["cached"] is False
        assert r2.json()["cached"] is True and mc.call_count == 1   # 第二次走缓存（归一后同 key）

    def test_analyze_rejects_viewer(self):
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        from src.web_api import auth as _auth
        with patch.object(_auth, "verify_jwt",
                          return_value={"sub": "2", "username": "v", "role": "viewer"}):
            client = TestClient(app)
            client.headers.update({"Authorization": "Bearer t"})
            assert client.post("/api/stock/600000.SH/analyze").status_code == 403
