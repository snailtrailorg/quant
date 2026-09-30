"""批 83a：配置面两族（数据源 / 交易账号）钉群——域词表/域过滤/写侧校验/单表拖拽/读点表名。

钉的是拆表后的行为契约：**域=表=端点族=列集=能力集**五者同源（markets.DOMAIN_*）；
原 external_interface 合表 + `'trading' = ANY(capabilities)` 域谓词整套退役。
DB 走 _FakeConn 惯例（test_rate_limit_ds 同款）——**但假连接不真执行 SQL**，
`INSERT ... VALUES (name=%s)` 这类**语句形态**错误 PG 永不校验（复审 P0：接口必然 500
而本文件全绿）。故**写路径另设真库往返钉** `TestRealDbRoundTrip`（无 dev 库自动跳过）——
形态类错误只有真库抓得住（记忆 handoff-no-shortcuts：行为级验收不可用 mock 绿替代）。

夹具约定：SQL 带表名（`data_source`/`trading_account`），故按表名分流并断言「打到了哪张表」——
拆表最典型的回归就是「某读点漏改仍读旧表」，这类必须能被断言抓住。
"""
import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _FakeConn:
    """可编程假连接：应答 fetchone/fetchall，记录全部执行；raise_on 模拟 DB 错误码。

    - one/all_rows：fetchone()/fetchall() 的固定应答（原惯例）
    - raise_on：命中的 SQL 片段 → 抛该异常（测 FK/唯一冲突的错误码映射）
    """

    def __init__(self, one=None, all_rows=None, raise_on=None):
        self.one = one
        self.all_rows = all_rows or []
        self.raise_on = raise_on or {}
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        self.executed.append((sql, args))
        for frag, exc in self.raise_on.items():
            if frag in sql:
                raise exc
        conn = self
        cur = type("C", (), {
            "fetchone": staticmethod(lambda: conn.one),
            "fetchall": staticmethod(lambda: list(conn.all_rows)),
        })()
        return cur

    def commit(self):
        pass

    def rollback(self):
        pass


def _db_error(sqlstate):
    e = RuntimeError(f"fake {sqlstate}")
    e.sqlstate = sqlstate
    return e


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
    """补 config_store.get_conn（83a：配置面 SQL 已下沉层 1——不再是 mgmt 的补丁点）。"""

    def __init__(self, one=None, all_rows=None, raise_on=None):
        import src.data_platform.config_store as cs
        self.conn = _FakeConn(one=one, all_rows=all_rows, raise_on=raise_on)
        self._p = patch.object(cs, "get_conn", lambda: self.conn)

    def __enter__(self):
        self._p.start()
        return self.conn

    def __exit__(self, *a):
        self._p.stop()
        return False


DATA_URL = "/api/data-sources"
TRADING_URL = "/api/trading-accounts"


# --- 注册表键（55-0 遗留） ---

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
        assert provider_capabilities("binance_perp") == {"trading", "rt_quote"}
        assert provider_capabilities("okx_perp") == {"trading", "rt_quote"}


# --- 域词表（83a 立法：域=表；两域能力集互斥且并集=CAPABILITIES） ---

class TestDomainVocabulary:

    def test_domains_partition_capabilities(self):
        """漏一个 token→某域行建不出；多一个→两域重叠（拆表合法性由此守）。"""
        from src.quant_common.markets import CAPABILITIES, DOMAIN_CAPS
        flat = [c for caps in DOMAIN_CAPS.values() for c in caps]
        assert len(flat) == len(set(flat))                  # 互斥
        assert sorted(flat) == sorted(CAPABILITIES)         # 并集=全集

    def test_domains_named_as_tables(self):
        """域词=表名（同源：DOMAIN_* 本身就是表名，消费侧零映射层）。"""
        from src.quant_common.markets import DOMAIN_DATA, DOMAIN_TRADING
        assert DOMAIN_DATA == "data_source" and DOMAIN_TRADING == "trading_account"

    def test_provider_domain_derivation(self):
        """provider 归属域按代码能力集推导；空能力 stub 源=None（两族目录都不出）。"""
        from src.data_platform.capabilities import provider_domain
        assert provider_domain("tushare") == "data_source"
        for p in ("xtp", "emt_emq", "binance_perp", "okx_perp", "tencent"):
            assert provider_domain(p) == "trading_account", p
        for p in ("joinquant", "ricequant"):     # stub：能力集空
            assert provider_domain(p) is None, p

    def test_check_capability_domain_rejects_cross_domain(self):
        """越域能力守卫（纯函数钉）。端点层当前不可达（无跨域 provider），但它是
        「拆表后每行单域」这条立法的写侧表达——未来跨域 provider 出现时是唯一拦截点。"""
        from src.data_platform.capabilities import check_capability_domain
        ok, _ = check_capability_domain("data_source", {"hist_quote", "ref_data"})
        assert ok
        ok, msg = check_capability_domain("data_source", {"hist_quote", "trading"})
        assert not ok and "trading" in msg
        ok, _ = check_capability_domain("trading_account", {"rt_quote", "trading"})
        assert ok
        ok, _ = check_capability_domain("trading_account", {"inst_event"})
        assert not ok


# --- 写侧校验（market 一致 / 域一致 / ⊆代码能力 / ⊆本域能力集） ---

class TestCreateValidation:

    def test_cap_excess_rejected(self, admin_client):
        """⊆代码能力：tushare 无 trading 能力。"""
        with _ConnPatch():
            r = admin_client.post(DATA_URL, json={
                "name": "t", "provider": "tushare", "market": "astock",
                "capabilities": ["hist_quote", "trading"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_CAP_EXCESS"

    def test_data_endpoint_rejects_trading_provider(self, admin_client):
        """域过滤：交易域 provider 不能经数据源端点建行（原合表可建，拆表后 400）。"""
        with _ConnPatch():
            r = admin_client.post(DATA_URL, json={
                "name": "t", "provider": "xtp", "market": "astock",
                "capabilities": ["rt_quote"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_DOMAIN_MISMATCH"

    def test_trading_endpoint_rejects_data_provider(self, admin_client):
        with _ConnPatch():
            r = admin_client.post(TRADING_URL, json={
                "name": "t", "provider": "tushare", "market": "astock",
                "capabilities": ["hist_quote"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_DOMAIN_MISMATCH"

    def test_market_mismatch_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post(TRADING_URL, json={
                "name": "t", "provider": "xtp", "market": "crypto",
                "capabilities": ["trading", "rt_quote"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_MARKET_MISMATCH"

    def test_exchange_out_of_market_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post(TRADING_URL, json={
                "name": "t", "provider": "xtp", "market": "astock",
                "exchanges": ["BINANCE"], "capabilities": ["trading"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_EXCHANGE_UNKNOWN"

    def test_empty_caps_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post(DATA_URL, json={
                "name": "t", "provider": "tushare", "market": "astock", "capabilities": []})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_CAP_EMPTY"

    def test_unknown_provider_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post(DATA_URL, json={
                "name": "t", "provider": "wind", "market": "astock", "capabilities": ["hist_quote"]})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_PROVIDER_UNKNOWN"

    def test_params_invalid_json_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post(DATA_URL, json={
                "name": "t", "provider": "tushare", "market": "astock",
                "capabilities": ["hist_quote"], "params": "{not-json"})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_PARAMS_INVALID"

    def test_params_non_object_json_rejected(self, admin_client):
        """盲审 A-P1/B-P1-1 修钉：合法 JSON 非对象（数组/标量）同拒 400——曾因缺 raise 致 500。"""
        for bad in ("[1,2]", "42", '"x"', "null"):
            with _ConnPatch():
                r = admin_client.post(DATA_URL, json={
                    "name": "t", "provider": "tushare", "market": "astock",
                    "capabilities": ["hist_quote"], "params": bad})
            assert r.status_code == 400 and r.json()["code"] == "IFACE_PARAMS_INVALID", bad

    def test_perp_default_exchanges_derived(self, admin_client):
        """盲审 B-P2-7 修钉：perp 缺省 exchanges=单所推导（防 NULL=全所语义错）。"""
        with _ConnPatch(one=(9,)) as conn:
            r = admin_client.post(TRADING_URL, json={
                "name": "t", "provider": "binance_perp", "market": "crypto",
                "capabilities": ["trading"]})
        assert r.status_code == 200
        args = conn.executed[0][1]
        assert args[3] == ["BINANCE"]   # exchanges 位=推导单所

    def test_create_position_per_table_sequence(self, admin_client):
        """position=**本表** max+1（83a 单表单序列）——两族各自打自己的表，无域谓词。"""
        cases = ((DATA_URL, "tushare", ["hist_quote"], "data_source"),
                 (TRADING_URL, "xtp", ["trading", "rt_quote"], "trading_account"))
        for url, provider, caps, tbl in cases:
            with _ConnPatch(one=(9,)) as conn:
                r = admin_client.post(url, json={
                    "name": "t", "provider": provider, "market": "astock", "capabilities": caps})
            assert r.status_code == 200, r.text
            sql = conn.executed[0][0]
            assert f"INSERT INTO {tbl}" in sql
            assert f"coalesce(max(position),-1)+1 FROM {tbl}" in sql
            assert "ANY(capabilities)" not in sql   # 单表序列无域谓词

    def test_data_create_has_no_exchanges_or_account_key(self, admin_client):
        """数据源行**无** exchanges/account_key 两列（83a 列集立法——写这两列=错表语义）。"""
        with _ConnPatch(one=(9,)) as conn:
            r = admin_client.post(DATA_URL, json={
                "name": "t", "provider": "tushare", "market": "astock",
                "capabilities": ["hist_quote"]})
        assert r.status_code == 200, r.text
        sql = conn.executed[0][0]
        assert "exchanges" not in sql and "account_key" not in sql

    def test_account_key_dup_mapped(self, admin_client):
        """account_key UNIQUE(provider,account_key) 冲突 → 409（错误码映射在路由层）。"""
        with _ConnPatch(one=(9,), raise_on={"INSERT INTO trading_account": _db_error("23505")}):
            r = admin_client.post(TRADING_URL, json={
                "name": "t", "provider": "xtp", "market": "astock",
                "capabilities": ["trading"], "account_key": "253191001822"})
        assert r.status_code == 409 and r.json()["code"] == "IFACE_ACCOUNT_KEY_DUP"

    def test_update_cannot_switch_domain(self, admin_client):
        """换域只能删了在另一族重建（合表时代的 IFACE_DOMAIN_CHANGE 校验随拆表自然退役）。"""
        with _ConnPatch():
            r = admin_client.post(f"{TRADING_URL}/4", json={
                "name": "t", "provider": "tushare", "market": "astock",
                "capabilities": ["hist_quote"]})       # 交易行欲改成数据源行
        assert r.status_code == 400 and r.json()["code"] == "IFACE_DOMAIN_MISMATCH"

    def test_update_narrowing_caps_ok(self, admin_client):
        """同族收窄能力合法（原合表「trading 行改数据能力」被拒的场景，拆表后不存在该路径）。"""
        with _ConnPatch(one=(4,)):
            r = admin_client.post(f"{TRADING_URL}/4", json={
                "name": "t", "provider": "xtp", "market": "astock",
                "capabilities": ["rt_quote"]})         # 去掉 trading，留行情
        assert r.status_code == 200, r.text

    def test_update_omits_credentials_when_blank(self, admin_client):
        """三段语义：credentials 空=不改（UPDATE 列集里不得出现 credentials_encrypted）。"""
        with _ConnPatch(one=(4,)) as conn:
            admin_client.post(f"{TRADING_URL}/4", json={
                "name": "t", "provider": "xtp", "market": "astock",
                "capabilities": ["trading"], "credentials": ""})
        upd = [sql for sql, _ in conn.executed if sql.startswith("UPDATE")]
        assert upd and "credentials_encrypted" not in upd[0]

    def test_update_not_found(self, admin_client):
        with _ConnPatch(one=None):
            r = admin_client.post(f"{DATA_URL}/999", json={
                "name": "t", "provider": "tushare", "market": "astock",
                "capabilities": ["hist_quote"]})
        assert r.status_code == 404 and r.json()["code"] == "IFACE_NOT_FOUND"


# --- 列表 / 过滤 / 域隔离 ---

_ROW_DATA = (1, "Tushare主", "tushare", "astock", True,
             {"rate_limits": {}}, ["hist_quote"], 0, True, None)      # 10 列（无 exchanges/account_key）
_ROW_XTP = (4, "中泰XTP", "xtp", "astock", None, True,
            {"td_host": "x"}, ["trading", "rt_quote"], 0, True, None, "253191001822")   # 12 列


class TestListEndpoints:

    def test_list_shape_and_code_caps(self, admin_client):
        """两族行形状统一（对方族的列给 None），前端表格/弹窗按 kind 决定是否渲染。"""
        with _ConnPatch(all_rows=[_ROW_DATA]):
            rd = admin_client.get(DATA_URL)
        with _ConnPatch(all_rows=[_ROW_XTP]):
            rt = admin_client.get(TRADING_URL)
        assert rd.status_code == 200 and rt.status_code == 200
        d, t = rd.json()[0], rt.json()[0]
        assert d["provider"] == "tushare" and d["exchanges"] is None and d["account_key"] is None
        assert t["exchanges"] is None and t["account_key"] == "253191001822"
        assert t["code_capabilities"] == ["rt_quote", "trading"]

    def test_list_hits_own_table_only(self, admin_client):
        """域隔离：数据源族只打 data_source，交易族只打 trading_account（漏改=串表回归）。"""
        with _ConnPatch(all_rows=[_ROW_DATA]) as c1:
            admin_client.get(DATA_URL)
        with _ConnPatch(all_rows=[_ROW_XTP]) as c2:
            admin_client.get(TRADING_URL)
        assert "FROM data_source" in c1.executed[0][0]
        assert "trading_account" not in c1.executed[0][0]
        assert "FROM trading_account" in c2.executed[0][0]
        assert "data_source" not in c2.executed[0][0]

    def test_cap_filter_uses_gin_containment(self, admin_client):
        with _ConnPatch(all_rows=[_ROW_DATA]) as conn:
            r = admin_client.get(DATA_URL, params={"cap": "hist_quote"})
        assert r.status_code == 200
        assert "capabilities @> ARRAY[%s]::text[]" in conn.executed[0][0]
        assert conn.executed[0][1] == ("hist_quote",)

    def test_cap_filter_domain_scoped(self, admin_client):
        """筛选值域=本域能力集：跨域 cap 深链 400（防「静默空表」被当成「真没有」）。"""
        with _ConnPatch(all_rows=[]):
            r = admin_client.get(DATA_URL, params={"cap": "trading"})
        assert r.status_code == 400 and r.json()["code"] == "IFACE_CAP_INVALID"
        with _ConnPatch(all_rows=[]):
            r2 = admin_client.get(TRADING_URL, params={"cap": "hist_quote"})
        assert r2.status_code == 400 and r2.json()["code"] == "IFACE_CAP_INVALID"


class TestProvidersDirectory:

    def test_data_directory_lists_data_providers_only(self, admin_client):
        r = admin_client.get(f"{DATA_URL}/providers")
        assert r.status_code == 200
        assert [p["provider"] for p in r.json()["providers"]] == ["tushare"]
        # 83a：目录随回本域能力集——前端能力筛选/勾选零字面量（同源 markets.DOMAIN_CAPS）
        assert r.json()["domain_capabilities"] == sorted(["hist_quote", "ref_data", "inst_event"])

    def test_trading_directory_lists_trading_providers_only(self, admin_client):
        r = admin_client.get(f"{TRADING_URL}/providers")
        assert r.status_code == 200
        got = [p["provider"] for p in r.json()["providers"]]
        assert got == sorted(["xtp", "emt_emq", "binance_perp", "okx_perp", "tencent"])
        assert "tushare" not in got                    # 数据源 provider 不出交易页签下拉
        assert "joinquant" not in got                  # 空能力 stub 源两族都不出目录
        assert r.json()["domain_capabilities"] == sorted(["rt_quote", "trading"])


# --- 单表拖拽重排（83a：原「全局单序列」退役） ---

class TestReorder:

    def test_empty_ids_rejected(self, admin_client):
        with _ConnPatch():
            r = admin_client.post(f"{DATA_URL}/reorder", json={"ids": []})
        assert r.status_code == 400 and r.json()["code"] == "BAD_PARAM"

    def test_not_full_set_rejected(self, admin_client):
        with _ConnPatch(all_rows=[(1,), (2,)]) as conn:
            r = admin_client.post(f"{DATA_URL}/reorder", json={"ids": [1]})
        assert r.status_code == 400 and r.json()["code"] == "BAD_PARAM"
        assert "FROM data_source" in conn.executed[0][0]   # 全量查询限本表

    def test_reorder_ids_of_other_table_rejected(self, admin_client):
        """跨表 id 撞车不串表：拿交易账号 id 去拖数据源表 → 全量校验必拒。"""
        with _ConnPatch(all_rows=[(1,)]) as conn:      # data_source 只有 id=1
            r = admin_client.post(f"{DATA_URL}/reorder", json={"ids": [4]})   # 4 属 trading_account
        assert r.status_code == 400 and r.json()["code"] == "BAD_PARAM"
        assert all("trading_account" not in sql for sql, _ in conn.executed)

    def test_reorder_renumbers_in_order(self, admin_client):
        with _ConnPatch(all_rows=[(1,), (2,)]) as conn:
            r = admin_client.post(f"{TRADING_URL}/reorder", json={"ids": [2, 1]})
        assert r.status_code == 200
        updates = [(sql, a) for sql, a in conn.executed if sql.startswith("UPDATE")]
        assert [a for _, a in updates] == [(0, 2), (1, 1)]   # 按提交序重编号 0..n-1
        assert all("trading_account" in sql for sql, _ in updates)


# --- 删除（预检分域：live_task 只关交易族） ---

class TestDelete:

    def test_trading_delete_blocked_by_live_task(self, admin_client):
        with _ConnPatch(one=(7,)) as conn:      # live_task 命中
            r = admin_client.delete(f"{TRADING_URL}/4")
        assert r.status_code == 409 and r.json()["code"] == "IFACE_IN_USE"
        assert "live_task" in conn.executed[0][0]

    def test_data_delete_never_checks_live_task(self, admin_client):
        """**拆表踩点**：两表 id 独立，数据源行 id 可能与某 live_task.account_id 数值相同——
        若数据源族也预检 live_task，删数据源行会被误判「该交易账号下有实盘任务」。"""
        with _ConnPatch(one=(7,)) as conn:      # live_task 应答存在（若被查即误拒）
            r = admin_client.delete(f"{DATA_URL}/1")
        assert r.status_code == 200, r.text
        assert all("live_task" not in sql for sql, _ in conn.executed)
        assert conn.executed[0][0].startswith("DELETE FROM data_source")

    def test_fk_violation_mapped(self, admin_client):
        with _ConnPatch(raise_on={"DELETE FROM data_source": _db_error("23503")}):
            r = admin_client.delete(f"{DATA_URL}/1")
        assert r.status_code == 409 and r.json()["code"] == "IFACE_IN_USE"


# --- 连接测试（按族路由注册表） ---

class TestConnectionTest:

    def test_trading_test_uses_broker_registry(self, admin_client):
        """交易族→Broker 注册表（quote-only 行回落 DataSource 覆盖）。"""
        from src.strategy_framework.broker import XTPBroker
        with _ConnPatch(one=("xtp", None, {}, ["trading", "rt_quote"])):
            with patch.object(XTPBroker, "test_connection", return_value=True):
                r = admin_client.post(f"{TRADING_URL}/4/test")
        assert r.status_code == 200 and r.json()["ok"] is True

    def test_data_test_uses_datasource_registry(self, admin_client):
        from src.data_platform.data_source import TushareDataSource
        with _ConnPatch(one=("tushare", None, {}, ["hist_quote"])):
            with patch.object(TushareDataSource, "test_connection", return_value=False):
                r = admin_client.post(f"{DATA_URL}/1/test")
        assert r.status_code == 200 and r.json()["ok"] is False

    def test_test_missing_row(self, admin_client):
        with _ConnPatch(one=None):
            r = admin_client.post(f"{DATA_URL}/999/test")
        assert r.status_code == 200 and r.json()["ok"] is False


# --- 读点表名钉（拆表最典型回归：读点漏改仍读旧表） ---

class TestReadPoints:

    def test_get_data_source_reads_data_source_no_domain_predicate(self):
        """数据源读点：打 data_source，**无**域谓词（表本身即域——83a 表层面退役）。"""
        import src.data_platform.data_source as ds_mod
        import src.data_platform.db as db_mod
        conn = _FakeConn(one=(3, "ENC", {"rate_limits": {"daily": 2}}))
        with patch.object(db_mod, "get_conn", lambda: conn):
            ds = ds_mod.get_data_source("tushare")
        assert ds is not None and ds.interface_id == 3
        sql = conn.executed[0][0]
        assert "FROM data_source" in sql
        assert "ANY(capabilities)" not in sql
        assert "ORDER BY position, id LIMIT 1" in sql

    def test_get_broker_reads_trading_account_no_domain_predicate(self):
        """交易读点：打 trading_account，无域谓词。"""
        import src.strategy_framework.broker as b_mod
        import src.data_platform.db as db_mod
        conn = _FakeConn(one=("ENC", {"client_id_runner": 5}))
        with patch.object(db_mod, "get_conn", lambda: conn):
            b = b_mod.get_broker("xtp")
        assert b is not None and b._params == {"client_id_runner": 5}
        sql = conn.executed[0][0]
        assert "FROM trading_account" in sql
        assert "ANY(capabilities)" not in sql
        assert "ORDER BY position, id LIMIT 1" in sql

    def test_get_interface_row_reads_trading_account(self):
        """hub/MD 网关按行取配置：打 trading_account（md_only=True 跳过凭证必填校验——
        本钉只关表名与列序，凭证 schema 校验另有专测）。"""
        from src.strategy_framework import broker as b_mod
        import src.data_platform.db as db_mod
        conn = _FakeConn(one=("xtp", None, {}, "astock", ["trading", "rt_quote"]))
        with patch.object(db_mod, "get_conn", lambda: conn):
            row = b_mod.get_interface_row(4, md_only=True)
        assert row["provider"] == "xtp" and row["market"] == "astock"
        assert "FROM trading_account WHERE id=%s" in conn.executed[0][0]

    def test_load_ds_params_reads_data_source_without_domain_predicate(self):
        """限流四层选行（三消费方①）：data_source 直读 + enabled DESC/position 序，无域谓词。"""
        import src.web_api.routes.mgmt as mgmt
        import src.data_platform.config_store as cs
        conn = _FakeConn(one=(5, {"rate_limits": {"daily": 2}}))
        with patch.object(cs, "get_conn", lambda: conn):
            dsid, params = mgmt._load_ds_params("tushare")
        assert (dsid, params) == (5, {"rate_limits": {"daily": 2}})
        sql = conn.executed[0][0]
        assert "FROM data_source" in sql
        assert "ORDER BY enabled DESC, position, id LIMIT 1" in sql
        assert "ANY(capabilities)" not in sql

    def test_load_ds_params_missing_row_404(self):
        import src.web_api.routes.mgmt as mgmt
        import src.data_platform.config_store as cs
        from src.web_api.errors import ApiError
        conn = _FakeConn(one=None)
        with patch.object(cs, "get_conn", lambda: conn):
            with pytest.raises(ApiError) as ei:
                mgmt._load_ds_params("tushare")
        assert ei.value.code == "DS_NOT_FOUND"

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


# --- account_permission（存在性判据改 trading_account） ---

class TestAccountPermission:

    def test_put_checks_trading_account(self, admin_client):
        with _ConnPatch(one=None) as conn:
            r = admin_client.put("/api/accounts/4/permission", json={
                "allowed_categories": ["stock"], "allowed_exchanges": ["SHSE"],
                "allowed_boards": ["main"]})
        assert r.status_code == 404 and r.json()["code"] == "ACCOUNT_NOT_FOUND"
        assert "FROM trading_account" in conn.executed[0][0]

    def test_put_bad_value_rejected_before_db(self, admin_client):
        with _ConnPatch(one=(4,)) as conn:
            r = admin_client.put("/api/accounts/4/permission", json={
                "allowed_categories": ["bogus"], "allowed_exchanges": ["SHSE"],
                "allowed_boards": ["main"]})
        assert r.status_code == 400 and r.json()["code"] == "ACCOUNT_PERM_BAD_VALUE"
        assert conn.executed == []          # 值域校验在查库之前

    def test_put_upserts_on_trading_account(self, admin_client):
        with _ConnPatch(one=(4,)) as conn:
            r = admin_client.put("/api/accounts/4/permission", json={
                "allowed_categories": ["stock"], "allowed_exchanges": ["SHSE"],
                "allowed_boards": ["main"], "is_st_allowed": True})
        assert r.status_code == 200
        assert any("INTO account_permission" in sql for sql, _ in conn.executed)

    def test_params_roundtrip_json_string(self):
        """params 双形态（JSON 字符串/对象）归一——批63 端点在两族同语义。"""
        from src.web_api.routes.mgmt import _normalize_params
        assert _normalize_params('{"a": 1}') == {"a": 1}
        assert _normalize_params({"a": 1}) == {"a": 1}
        assert _normalize_params("") == {} and _normalize_params(None) == {}
        assert json.loads(json.dumps(_normalize_params('{"a": 1}'))) == {"a": 1}


# --- 真库往返（批 83a 复审 P0 回归钉：SQL 形态错误 mock 绿抓不到） ---

def _db_up() -> bool:
    """dev 库可达？（无库自动跳过——test_account_permission 同款惯例）"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestRealDbRoundTrip:
    """config_store 写路径**真库**往返：insert → 读回 → update → delete（两族各一遍）。

    复审 P0 的回归钉：`insert_row` 曾把 UPDATE 的 `_ph`（返回 `col=%s`）复用到 VALUES，
    生成 `VALUES (name=%s, ...)` → PG 报 `column "name" does not exist`，**新建数据源/
    交易账号接口必然 500**，而本文件其余 _FakeConn 用例全绿（假连接不真执行 SQL，
    语句形态错误 PG 永不校验）。故形态类错误必须在真库钉住。
    """

    @staticmethod
    def _mk_values(kind, cs, tag):
        """两族各造一份合法 values（params 走 JSON 字符串——insert_row 入参契约）。"""
        vals = {"name": f"RT-{tag}", "provider": cs.KIND_DATA == kind and "tushare" or "xtp",
                "market": "astock", "params": json.dumps({"rate_limits": {"a": 1}}),
                "capabilities": ["hist_quote"] if kind == cs.KIND_DATA else ["trading"],
                "enabled": True}
        if kind == cs.KIND_TRADING:
            vals["exchanges"] = ["SHSE"]
            vals["account_key"] = f"RT-{tag}"          # UNIQUE(provider, account_key)——uuid 唯一
        return vals

    @staticmethod
    def _read(kind, cs, rid):
        return next(cs.row_dict(r, kind) for r in cs.list_rows(kind) if r[0] == rid)

    _KINDS = ["data_source", "trading_account"]

    @pytest.mark.parametrize("kind", _KINDS)
    def test_insert_read_update_delete(self, kind):
        """写全链路真库往返——INSERT 语句形态正确性（P0）+ jsonb/数组往返 + 列集差异。"""
        import uuid
        from src.data_platform import config_store as cs
        tag = uuid.uuid4().hex[:8]
        rid = None
        try:
            vals = self._mk_values(kind, cs, tag)
            # INSERT 是本钉的核心：形态错误在此抛 UndefinedColumn（不再假绿）
            rid = cs.insert_row(kind, vals)
            assert isinstance(rid, int) and rid > 0

            d = self._read(kind, cs, rid)
            assert d["name"] == f"RT-{tag}" and d["enabled"] is True
            assert d["market"] == "astock" and d["provider"] == vals["provider"]
            assert d["params"] == {"rate_limits": {"a": 1}}      # jsonb 往返成 dict
            assert d["capabilities"] == vals["capabilities"]     # text[] 往返
            assert d["has_credentials"] is False
            if kind == cs.KIND_TRADING:
                assert d["exchanges"] == ["SHSE"]                # 交易族专有两列
                assert d["account_key"] == f"RT-{tag}"
            else:
                assert d["exchanges"] is None and d["account_key"] is None   # 数据源族无此二列

            # UPDATE：params 亦走 SET 占位符（两条 SQL 的占位符形态各自独立）
            cs.update_row(kind, rid, {"name": f"RT2-{tag}", "params": json.dumps({"b": 2})})
            d2 = self._read(kind, cs, rid)
            assert d2["name"] == f"RT2-{tag}"
            assert d2["params"] == {"b": 2}
            assert d2["capabilities"] == vals["capabilities"]     # 未提交的列不动

            # DELETE
            cs.delete_row(kind, rid)
            assert cs.row_exists(kind, rid) is False
            rid = None
        finally:
            if rid is not None and cs.row_exists(kind, rid):
                cs.delete_row(kind, rid)                          # 断言失败也不留脏行

    @pytest.mark.parametrize("kind", _KINDS)
    def test_position_is_per_table_sequence(self, kind):
        """position=**本表**单序（拆表立法）：连插两行 position 严格递增，且不看另一张表。"""
        import uuid
        from src.data_platform import config_store as cs
        other = cs.KIND_TRADING if kind == cs.KIND_DATA else cs.KIND_DATA
        ids = []
        try:
            tag = uuid.uuid4().hex[:8]
            for i in (1, 2):
                ids.append(cs.insert_row(kind, self._mk_values(kind, cs, f"{tag}{i}")))
            pos = [self._read(kind, cs, i)["position"] for i in ids]
            assert pos[1] == pos[0] + 1                           # 本表内严格递增
            # 另一张表的 position 与本表无关（同值可各自存在=独立序列，不跨表推断）
            other_max = max([r for r in cs.list_rows(other)], key=lambda r: r[0])[0] if cs.list_rows(other) else None
            ids_other = {r[0] for r in cs.list_rows(other)}
            assert ids[0] not in ids_other                        # 两表 id 空间独立（禁跨表按 id 推断）
            del other_max
        finally:
            for i in ids:
                if cs.row_exists(kind, i):
                    cs.delete_row(kind, i)

    def test_update_omits_credentials_keeps_value(self):
        """三段语义真库钉：UPDATE 不带 credentials_encrypted = 不改（不是清空）。"""
        import uuid
        from src.data_platform import config_store as cs
        from src.quant_common.crypto import encrypt
        rid = None
        try:
            tag = uuid.uuid4().hex[:8]
            vals = self._mk_values(cs.KIND_DATA, cs, tag)
            vals["credentials_encrypted"] = encrypt(json.dumps({"token": "seed-token"}))
            rid = cs.insert_row(cs.KIND_DATA, vals)
            assert self._read(cs.KIND_DATA, cs, rid)["has_credentials"] is True

            cs.update_row(cs.KIND_DATA, rid, {"name": f"RT3-{tag}"})   # 故意不带 credentials
            d = self._read(cs.KIND_DATA, cs, rid)
            assert d["has_credentials"] is True                       # 保留，未被清空
            assert d["name"] == f"RT3-{tag}"
            assert cs.get_conn_row(cs.KIND_DATA, rid)[1] is not None   # 密文列未被动过
        finally:
            if rid is not None and cs.row_exists(cs.KIND_DATA, rid):
                cs.delete_row(cs.KIND_DATA, rid)
