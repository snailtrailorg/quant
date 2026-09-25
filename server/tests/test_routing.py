"""批 57·M2 路由内核测试（29 号 §五验收判据 ①~⑥）。

mock 姿势（任务文件）：_FakeConn 构造账号行+mock redis+SMClient covers 打桩——无库可跑；
验收①的 dev 真库 dry-run 另挂 skip（行为级）。
"""
import os
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.quant_common.contract import DataRequest, DataGap, SourceUnavailable


class _FakeConn:
    """routing 两个 SELECT 的构造行应答（external_interface+routing_policy+routing_decision insert）。"""

    def __init__(self, rows, policies):
        self.rows, self.policies = rows, policies
        self.inserts = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        cur = MagicMock()
        if "FROM external_interface" in sql:
            cur.description = [(d,) for d in ("provider", "market", "exchanges",
                                              "capabilities", "params", "position", "enabled")]
            cur.fetchall.return_value = self.rows
        elif "FROM routing_policy" in sql:
            cur.description = [("consumer_tag",), ("weights",), ("bulkhead_defaults",)]
            cur.fetchall.return_value = self.policies
        elif "INSERT INTO routing_decision" in sql:
            self.inserts.append(params)
            cur.fetchone.return_value = None
        else:
            raise AssertionError(f"未预期的 SQL: {sql[:60]}")
        return cur

    def commit(self):
        pass


_ROWS = [  # D25 token 化后真实形状：tushare{hist_quote}+tencent{rt_quote}+xtp{trading,rt_quote}
    ("tushare", "astock", None, ["hist_quote"], "{}", 1, True),
    ("tencent", "astock", None, ["rt_quote"], "{}", 2, True),
    ("xtp", "astock", None, ["trading", "rt_quote"], "{}", 3, True),
]


class TestCapCoversD25:
    """D25 类 token 展开行为钉（盲审 A-P1/B-P1：核心新逻辑零覆盖——旧词夹具假绿教训）。

    直接钉 _cap_covers 纯函数：新 token 展开的命中/排除是路由选源的生死线。"""

    def test_hist_quote_covers_bar_family(self):
        from src.data_platform.routing import _cap_covers
        for kind in ("bar_daily", "bar_minute", "index_daily", "adj_factor"):
            assert _cap_covers(["hist_quote"], kind), kind

    def test_rt_quote_not_cover_bar(self):
        from src.data_platform.routing import _cap_covers
        # tencent 行 {rt_quote} 不得入选历史类 kind（盲审 A-P0 的行为面：剥 minute 后只剩 rt_quote）
        for kind in ("bar_daily", "bar_minute", "index_daily"):
            assert not _cap_covers(["rt_quote"], kind), kind

    def test_rt_quote_covers_stream_kinds(self):
        from src.data_platform.routing import _cap_covers
        # xtp 行 {trading, rt_quote} 展开后覆盖实时类 kind
        for kind in ("snapshot", "stream_bar", "stream_tick", "depth"):
            assert _cap_covers(["trading", "rt_quote"], kind), kind

    def test_ref_data_and_inst_event(self):
        from src.data_platform.routing import _cap_covers
        assert _cap_covers(["ref_data"], "fundamental_daily")
        assert _cap_covers(["ref_data"], "trade_cal")
        assert _cap_covers(["inst_event"], "suspend")
        assert not _cap_covers(["inst_event"], "bar_daily")

    def test_kline_alias_and_bare_kind_compat(self):
        from src.data_platform.routing import _cap_covers
        assert _cap_covers(["kline"], "bar_daily")            # 55a 历史别名
        assert _cap_covers(["bar_daily"], "bar_daily")        # 裸 DataKind 词兼容
_POLICIES = [
    ("default", {"completeness": 0.5, "cost": 0.3, "latency": 0.2}, None),
    ("backtest", {"completeness": 0.7, "cost": 0.2, "latency": 0.1}, None),
    ("sync", {"completeness": 0.5, "cost": 0.3, "latency": 0.2}, None),
]


class _RedisMock:
    def __init__(self):
        self.kv = {}
        self.counter = 0

    def get(self, k):
        return self.kv.get(k)

    def incr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) + 1
        return self.kv[k]

    def decr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) - 1
        return self.kv[k]

    def expire(self, k, ttl):
        return True


def _setup(rows=None, policies=None):
    """routing 全链 mock：真 _load_state 走 patched get_conn/_r（_STATE.rows 清空触发重载）。"""
    import src.data_platform.routing as rt
    conn = _FakeConn(rows or _ROWS, policies or _POLICIES)
    rds = _RedisMock()
    rds.kv[rt.CFG_VERSION_KEY] = "7"
    rt._STATE.rows = []          # 触发重载（get_table→_load_state 全走 mock conn）
    patches = [
        patch.object(rt, "get_conn", return_value=conn),
        patch.object(rt, "_r", return_value=rds),
    ]
    return rt, conn, rds, patches


@pytest.fixture
def sm_covers_all():
    with patch("src.data_platform.security_master.SMClient") as SM:
        SM.return_value.covers.return_value = True
        yield SM


@pytest.fixture
def breakers_clean():
    from src.data_platform import rate_limit
    with patch.object(rate_limit, "_BREAKERS", {}):
        yield


class TestResolve:
    def test_consume_local_first_then_tushare(self, sm_covers_all, breakers_clean):
        """验收①：bar_daily+600000+backtest → [local_pg, tushare①]（tencent quote-only 被硬过滤）。"""
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", consumer_tag="backtest")
            ch = rt.resolve(req)
        assert [c.adapter for c in ch.candidates] == ["local_pg", "tushare"]
        assert ch.candidates[0].is_local and not ch.candidates[1].is_local

    def test_supply_excludes_local(self, sm_covers_all, breakers_clean):
        """验收⑤：供给模式链无 local_pg（仓=对账基准非货源——28 §7.1 v3.2）。"""
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", consumer_tag="sync", mode="supply")
            ch = rt.resolve(req)
        assert [c.adapter for c in ch.candidates] == ["tushare"]

    def test_position_change_epoch(self, sm_covers_all, breakers_clean):
        """验收②：position 改动（provider 沉后）下新链生效+epoch 递增。"""
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",), temporality="historical")
            rows2 = [("tencent", "astock", None, ["kline"], "{}", 1, True),
                     ("tushare", "astock", None, ["kline"], "{}", 2, True)]
            conn2 = _FakeConn(rows2, _POLICIES)
            with patch.object(rt, "get_conn", return_value=conn2):
                rt._STATE.rows = []          # 强制重载（真 _load_state 读 conn2）
                ch = rt.resolve(req)
            assert [c.adapter for c in ch.candidates if not c.is_local] == ["tencent", "tushare"]

    def test_position_change_epoch_bumps(self, sm_covers_all, breakers_clean):
        """验收② epoch 半边：bump cfg:version 后新链 epoch 递增（原钉只断链序——盲审 A/B 补）。"""
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",), temporality="historical")
            e1 = rt.resolve(req).epoch
            rds.kv[rt.CFG_VERSION_KEY] = "8"          # bump（新版本）
            rt._STATE.rows = []                        # 强制重载
            e2 = rt.resolve(req).epoch
        assert e1 == 7 and e2 == 8

    def test_breaker_sink_to_bottom(self, sm_covers_all, breakers_clean):
        """验收③：熔断账号沉底（注入 open——保护机制复用为决策输入，28 §6.4）。"""
        from src.data_platform import rate_limit
        from src.data_platform.rate_limit import CircuitBreaker
        rt, conn, rds, patches = _setup()
        rows = [("tushare", "astock", None, ["kline"], "{}", 1, True),
                ("akshare", "astock", None, ["kline"], "{}", 2, True)]
        conn.rows = rows
        b = CircuitBreaker(fail_threshold=2, reset_timeout=300)
        b.record_failure(); b.record_failure()  # 真实触发：连续失败≥阈值 → Open
        with patches[0], patches[1], \
             patch.object(rate_limit, "_BREAKERS", {"tushare": b}):
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", mode="supply")
            ch = rt.resolve(req)
        assert [c.adapter for c in ch.candidates] == ["akshare", "tushare"]   # 熔断沉底不剔除

    def test_disabled_row_filtered(self, sm_covers_all, breakers_clean):
        rt, conn, rds, patches = _setup()
        conn.rows = [("tushare", "astock", None, ["kline"], "{}", 1, False)]
        with patches[0], patches[1]:
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", mode="supply")
            ch = rt.resolve(req)
        assert ch.candidates == ()

    def test_audit_req_summary_readable(self, sm_covers_all, breakers_clean):
        """验收⑥：审计行 req_summary 可读（kind+symbols 数+mode+consumer）。"""
        import json
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            rt.resolve(DataRequest(kind="bar_daily", symbols=("600000.SHSE", "510300.SHSE"),
                                   temporality="historical", consumer_tag="backtest"))
        [p] = conn.inserts
        summary = json.loads(p[1])
        assert summary["kind"] == "bar_daily" and summary["n_symbols"] == 2
        assert summary["consumer"] == "backtest" and summary["mode"] == "consume"
        assert p[4] == "resolve"


class TestFetchChain:
    def test_failover_and_datagap(self, sm_covers_all, breakers_clean):
        """验收④a：failover 审计——SourceUnavailable 链下移到次候选。"""
        rt, conn, rds, patches = _setup()
        conn.rows = [("tushare", "astock", None, ["kline"], "{}", 1, True),
                     ("akshare", "astock", None, ["kline"], "{}", 2, True)]
        rt._STATE.rows = []
        with patches[0], patches[1]:
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", mode="supply")
            ch = rt.resolve(req)
            calls = []

            def fetch_fn(c):
                calls.append(c.adapter)
                if c.adapter == "tushare":
                    raise SourceUnavailable("模拟源超时")
                return {"from": c.adapter}

            r = ch.fetch(fetch_fn, req)
        assert r == {"from": "akshare"} and calls == ["tushare", "akshare"]
        causes = [p[4] for p in conn.inserts]
        assert "failover" in causes

    def test_skip_busy_bulkhead(self, sm_covers_all, breakers_clean):
        """验收④b：skip_busy——bulkhead 闸门满（mock incr 恒返回超限）→审计+下移。"""
        rt, conn, rds, patches = _setup()
        conn.rows = [("tushare", "astock", None, ["kline"], "{}", 1, True),
                     ("akshare", "astock", None, ["kline"], "{}", 2, True)]

        class _FullRedis(_RedisMock):
            def incr(self, k):
                return 99 if "tushare" in k else super().incr(k)   # 仅 tushare 恒超闸（limit≤8）
        rds_full = _FullRedis()
        rds_full.kv[rt.CFG_VERSION_KEY] = "7"
        with patches[0], patch.object(rt, "_r", return_value=rds_full):
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", mode="supply")
            ch = rt.resolve(req)
            r = ch.fetch(lambda c: {"from": c.adapter}, req)
        assert r == {"from": "akshare"}
        causes = [p[4] for p in conn.inserts]
        assert "skip_busy" in causes

    def test_skip_unhealthy_no_leak(self, sm_covers_all, breakers_clean):
        """盲审 A/B P1 修复钉：熔断候选 fetch 时刻跳过且不占闸门（计数不泄漏）。"""
        from src.data_platform import rate_limit
        from src.data_platform.rate_limit import CircuitBreaker
        rt, conn, rds, patches = _setup()
        conn.rows = [("tushare", "astock", None, ["kline"], "{}", 1, True)]
        b = CircuitBreaker(fail_threshold=2, reset_timeout=300)
        b.record_failure(); b.record_failure()
        with patches[0], patches[1], \
             patch.object(rate_limit, "_BREAKERS", {"tushare": b}):
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", mode="supply")
            ch = rt.resolve(req)
            with pytest.raises(Exception):
                ch.fetch(lambda c: (_ for _ in ()).throw(RuntimeError("never")), req)
        assert rds.kv.get("routing:bh:tushare", 0) == 0      # 熔断源从未 admit——零计数
        causes = [p[4] for p in conn.inserts]
        assert "skip_unhealthy" in causes and "skip_busy" not in causes

    def test_all_gap_raises_datagap(self, sm_covers_all, breakers_clean):
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", mode="supply")
            ch = rt.resolve(req)

            def fetch_fn(c):
                raise DataGap("无此数据")

            with pytest.raises(DataGap):
                ch.fetch(fetch_fn, req)

    def test_deadline_exhausted(self, sm_covers_all, breakers_clean):
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            req = DataRequest(kind="bar_daily", symbols=("600000.SHSE",),
                              temporality="historical", mode="supply", deadline_ms=0)
            ch = rt.resolve(req)
            with pytest.raises(SourceUnavailable):
                ch.fetch(lambda c: {"ok": True}, req)   # deadline=0 → 首候选即超时全链尽


class TestBulkhead:
    def test_admit_reject_releases_slot(self):
        """被拒回减：超闸请求不留计数（防虚高至 TTL）。"""
        rt, conn, rds, patches = _setup()
        with patches[0], patch.object(rt, "_r", return_value=rds):
            rds.kv["routing:bh:tushare"] = 8             # 已满（默认限 8）
            assert rt._bulkhead_admit("tushare") is False
            assert rds.kv["routing:bh:tushare"] == 8     # incr 9→拒→decr 回 8

    def test_local_pg_no_gate(self):
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            assert rt._bulkhead_admit("local_pg") is True
            assert rt._bulkhead_release("local_pg") is None


class TestEndpoints:
    """端点级钉（函数直调绕 Depends——payload={}；conn 走 mock）。"""

    def test_update_policy_validation(self):
        import src.web_api.routes.routing as rr
        import pytest as _pytest
        from src.web_api.errors import ApiError
        with _pytest.raises(ApiError) as e:
            rr.update_policy("default", {"weights": {"completeness": 1}}, payload={})
        assert e.value.status_code == 400
        with _pytest.raises(ApiError) as e2:
            rr.update_policy("default", {"weights": {"completeness": "x", "cost": 1, "latency": 1}}, payload={})
        assert e.value.status_code == 400 and e2.value.status_code == 400

    def test_update_policy_404_and_ok(self):
        import src.web_api.routes.routing as rr
        import pytest as _pytest
        from src.web_api.errors import ApiError
        import src.data_platform.routing as rt

        class _UpdConn(_FakeConn):
            def execute(self, sql, params=None):
                cur = MagicMock()
                if "UPDATE routing_policy" in sql:
                    cur.fetchone.return_value = (params[-1],) if params[-1] == "default" else None
                    return cur
                return super().execute(sql, params)

        import src.web_api.routes.routing as rr_mod
        conn = _UpdConn(_ROWS, _POLICIES)
        rds = _RedisMock()
        with patch.object(rr_mod, "get_conn", return_value=conn), \
             patch.object(rt, "_r", return_value=rds):   # bump 走 routing 模块 _r
            r = rr.update_policy("default", {"weights": {"completeness": 1, "cost": 0, "latency": 0}}, payload={"username": "t"})
            assert r["ok"] and r["epoch"] == 1          # bump_config_version INCR 1
            with _pytest.raises(ApiError) as e:
                rr.update_policy("ghost", {"weights": {"completeness": 1, "cost": 0, "latency": 0}}, payload={"username": "t"})
            assert e.value.status_code == 404

    def test_dry_run_endpoint_shape(self, sm_covers_all, breakers_clean):
        import src.web_api.routes.routing as rr
        rt, conn, rds, patches = _setup()
        with patches[0], patches[1]:
            r = rr.dry_run(kind="bar_daily", symbol="600000.SHSE", consumer="backtest", payload={})
        assert r["epoch"] == 7 and r["weights"]["completeness"] == 0.7
        assert [c["adapter"] for c in r["chain"]] == ["local_pg", "tushare"]
        assert r["chain"][0]["is_local"] is True and r["chain"][0]["health"] == "closed"
