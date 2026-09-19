"""批55a：external_interface 统一表钉群——端点族/写侧校验/分域 reorder/垫片/键修正。

钉的是行为契约（27 号架构文档：行=账号/列=能力/页签=过滤视图）；
DB 走 _FakeConn 惯例（test_rate_limit_ds 同款），真跑验证在 dev 实测面。
"""
import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _FakeConn:
    """可编程假连接：按 SQL 前缀路由 fetchone/fetchall 结果，记录全部执行。"""

    def __init__(self, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        self.executed.append((sql, args))
        conn = self
        cur = type("C", (), {
            "fetchone": staticmethod(lambda: conn.one),
            "fetchall": staticmethod(lambda: list(conn.all_rows)),
        })()
        return cur

    def commit(self):
        pass


@pytest.fixture
def admin_client():
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    from src.web_api import auth as _auth
    with patch.object(_auth, "verify_jwt",
                      return_value={"sub": "1", "username": "admin", "role": "admin"}):
        client = TestClient(app)
        client.headers.update({"Authorization": "Bearer test-token"})
        yield client


class _ConnPatch:
    def __init__(self, one=None, all_rows=None):
        import src.web_api.routes.mgmt as mgmt
        self.conn = _FakeConn(one=one, all_rows=all_rows)
        self._p = patch.object(mgmt, "get_conn", lambda: self.conn)

    def __enter__(self):
        self._p.start()
        return self.conn

    def __exit__(self, *a):
        self._p.stop()
        return False


# --- 注册表键修正钉（55-0 遗留：NON_DATA_PROVIDERS 键须=真实 provider 串） ---

class TestRegistryKeys:

    def test_provider_universe_single_source(self):
        """全部注册通道键 ⊆ PROVIDER_MARKET（防 binance/okx vs binance_perp/okx_perp 再漂移）。"""
        from src.quant_common.markets import PROVIDER_MARKET, NON_DATA_PROVIDERS
        from src.strategy_framework.broker import _REGISTRY as brokers
        from src.data_platform.data_source import _REGISTRY as data_sources
        universe = set(brokers) | set(data_sources) | set(NON_DATA_PROVIDERS)
        assert universe <= set(PROVIDER_MARKET), universe - set(PROVIDER_MARKET)

    def test_perp_caps_after_key_fix(self):
        from src.data_platform.capabilities import provider_capabilities
        assert provider_capabilities("binance_perp") == {"trading"}
        assert provider_capabilities("okx_perp") == {"trading"}


# --- 写侧校验（六必修：⊆ 代码能力 / market 一致 / 交易所归属） ---

class TestCreateValidation:

    def test_cap_excess_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post("/api/interfaces", json={
                "name": "t", "provider": "tushare", "market": "astock",
                "capabilities": ["daily", "trading"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_CAP_EXCESS"

    def test_market_mismatch_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post("/api/interfaces", json={
                "name": "t", "provider": "xtp", "market": "crypto",
                "capabilities": ["trading", "quote"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_MARKET_MISMATCH"

    def test_exchange_out_of_market_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post("/api/interfaces", json={
                "name": "t", "provider": "tushare", "market": "astock",
                "exchanges": ["BINANCE"], "capabilities": ["daily"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_EXCHANGE_UNKNOWN"

    def test_empty_caps_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post("/api/interfaces", json={
                "name": "t", "provider": "tushare", "market": "astock", "capabilities": []})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_CAP_EMPTY"

    def test_unknown_provider_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post("/api/interfaces", json={
                "name": "t", "provider": "wind", "market": "astock", "capabilities": ["daily"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_PROVIDER_UNKNOWN"

    def test_params_invalid_json_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post("/api/interfaces", json={
                "name": "t", "provider": "tushare", "market": "astock",
                "capabilities": ["daily"], "params": "{not-json"})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_PARAMS_INVALID"

    def test_params_non_object_json_rejected(self, admin_client):
        """盲审 A-P1/B-P1-1 修钉：合法 JSON 非对象（数组/标量）同拒 400——曾因缺 raise 致 500。"""
        for bad in ("[1,2]", "42", '"x"', "null"):
            with _ConnPatch():
                r = admin_client.post("/api/interfaces", json={
                    "name": "t", "provider": "tushare", "market": "astock",
                    "capabilities": ["daily"], "params": bad})
            assert r.status_code == 400 and r.json()["code"] == "IFACE_PARAMS_INVALID", bad

    def test_update_domain_change_rejected(self, admin_client):
        """盲审 B-P1-2 后半修钉：改能力致换域拒（position 残留+选行序破坏）。"""
        with _ConnPatch(one=(["trading", "quote"],)):   # 现行=交易域
            r = admin_client.post("/api/interfaces/2", json={
                "name": "t", "provider": "xtp", "market": "astock",
                "capabilities": ["quote"]})             # 改后=数据域
        assert r.status_code == 400 and r.json()["code"] == "IFACE_DOMAIN_CHANGE"

    def test_perp_default_exchanges_derived(self, admin_client):
        """盲审 B-P2-7 修钉：perp 缺省 exchanges=单所推导（防 NULL=全所语义错）。"""
        with _ConnPatch(one=(9,)) as conn:
            r = admin_client.post("/api/interfaces", json={
                "name": "t", "provider": "binance_perp", "market": "crypto",
                "capabilities": ["trading"]})
        assert r.status_code == 200
        args = conn.executed[0][1]
        assert args[3] == ["BINANCE"]   # exchanges 位=推导单所

    def test_create_position_in_own_domain(self, admin_client):
        """position 子查询按域隔离：数据行走数据域谓词、交易行走交易域谓词。"""
        for caps, frag in ((["daily"], "NOT ('trading' = ANY(capabilities))"),
                           (["trading", "quote"], "'trading' = ANY(capabilities)")):
            with _ConnPatch(one=(9,)) as conn:
                r = admin_client.post("/api/interfaces", json={
                    "name": "t", "provider": ("tushare" if caps[0] == "daily" else "xtp"),
                    "market": "astock", "capabilities": caps})
            assert r.status_code == 200, r.text
            sql = conn.executed[0][0]
            assert "coalesce(max(position),-1)+1" in sql and frag in sql


# --- 列表/过滤/漂移告警 ---

_ROW_TUSHARE = (1, "Tushare主", "tushare", "astock", ["SHSE", "SZSE", "BSE"], True,
                {"rate_limits": {}}, ["daily"], 0, True, None)
_ROW_XTP = (2, "中泰XTP", "xtp", "astock", None, True,
            {"td_host": "x"}, ["trading", "quote"], 0, True, None)


class TestListEndpoints:

    def test_list_shape_and_code_caps(self, admin_client):
        with _ConnPatch(all_rows=[_ROW_TUSHARE, _ROW_XTP]):
            r = admin_client.get("/api/interfaces")
        assert r.status_code == 200
        items = r.json()
        assert [i["provider"] for i in items] == ["tushare", "xtp"]
        assert items[0]["params"] == {"rate_limits": {}}
        assert items[1]["code_capabilities"] == ["quote", "trading"]

    def test_cap_filter_uses_gin_containment(self, admin_client):
        with _ConnPatch(all_rows=[_ROW_TUSHARE]) as conn:
            r = admin_client.get("/api/interfaces", params={"cap": "daily"})
        assert r.status_code == 200
        assert "capabilities @> ARRAY[%s]::text[]" in conn.executed[0][0]
        assert conn.executed[0][1] == ("daily",)


# --- 分域 reorder（批43 全集语义 × 方案一 v2 分域段正交） ---

class TestReorder:

    def test_domain_invalid(self, admin_client):
        r = admin_client.post("/api/interfaces/reorder", json={"domain": "bogus", "ids": [1]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_DOMAIN_INVALID"

    def test_not_full_set_rejected(self, admin_client):
        with _ConnPatch(all_rows=[(1,), (2,)]) as conn:
            r = admin_client.post("/api/interfaces/reorder",
                                  json={"domain": "data", "ids": [1]})
        assert r.status_code == 400 and r.json()["code"] == "BAD_PARAM"
        assert "NOT ('trading' = ANY(capabilities))" in conn.executed[0][0]

    def test_reorder_renumbers_in_order(self, admin_client):
        with _ConnPatch(all_rows=[(1,), (2,)]) as conn:
            r = admin_client.post("/api/interfaces/reorder",
                                  json={"domain": "data", "ids": [2, 1]})
        assert r.status_code == 200
        updates = [(sql, a) for sql, a in conn.executed if sql.startswith("UPDATE")]
        assert [a for _, a in updates] == [(0, 2), (1, 1)]   # 按提交序重编号 0..n-1

    def test_trading_domain_query(self, admin_client):
        with _ConnPatch(all_rows=[(2,)]) as conn:
            r = admin_client.post("/api/interfaces/reorder",
                                  json={"domain": "trading", "ids": [2]})
        assert r.status_code == 200
        assert "'trading' = ANY(capabilities)" in conn.executed[0][0]


# --- 三消费方（勘察 #1/#3/#5） ---

class TestConsumers:

    def test_load_ds_params_domain_and_order(self, admin_client):
        """限流选行：数据域过滤 + enabled DESC, position, id 序（终裁 provider+position）。"""
        import src.web_api.routes.mgmt as mgmt
        from src.web_api.routes.mgmt import _load_ds_params
        conn = _FakeConn(one=(5, {"rate_limits": {"daily": 2}}))
        with patch.object(mgmt, "get_conn", lambda: conn):
            dsid, params = _load_ds_params("tushare")
        assert (dsid, params) == (5, {"rate_limits": {"daily": 2}})
        sql = conn.executed[0][0]
        assert "external_interface" in sql
        assert "ORDER BY enabled DESC, position, id LIMIT 1" in sql
        assert "NOT ('trading' = ANY(capabilities))" in sql

    def test_record_usage_dual_fill(self):
        from src.data_platform.data_source import TushareDataSource
        ds = TushareDataSource(interface_id=7)
        import src.data_platform.db as db_mod
        conn = _FakeConn()
        with patch.object(db_mod, "get_conn", lambda: conn):
            ds.record_usage(api_name="get_pro")
        sql, args = conn.executed[0]
        assert "interface_id" in sql and args[5] == 7          # 双填：键迁移落地
        conn2 = _FakeConn()
        with patch.object(db_mod, "get_conn", lambda: conn2):
            TushareDataSource().record_usage(api_name="get_pro")
        assert conn2.executed[0][1][5] is None                  # 裸构造（.env fallback）=NULL

    def test_get_data_source_jsonb_boundary(self):
        """jsonb dict→str 边界：实例 _params 可用 + interface_id 注入。"""
        import src.data_platform.data_source as ds_mod
        import src.data_platform.db as db_mod
        conn = _FakeConn(one=(3, "ENC", {"rate_limits": {"daily": 2}}))
        with patch.object(db_mod, "get_conn", lambda: conn):
            ds = ds_mod.get_data_source("tushare")
        assert ds is not None
        assert ds._params == {"rate_limits": {"daily": 2}}
        assert ds.interface_id == 3
        assert ds.get_rate_limit("daily") == 2.0

    def test_get_broker_jsonb_boundary(self):
        import src.strategy_framework.broker as b_mod
        import src.data_platform.db as db_mod
        conn = _FakeConn(one=("ENC", {"client_id_runner": 5}))
        with patch.object(db_mod, "get_conn", lambda: conn):
            b = b_mod.get_broker("xtp")
        assert b is not None
        assert b._params == {"client_id_runner": 5}
        sql = conn.executed[0][0]
        assert "'trading' = ANY(capabilities)" in sql
        assert "ORDER BY position, id LIMIT 1" in sql
