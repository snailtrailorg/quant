"""批 137b：池族对账（按表族判）+ 时间盒无状态排序。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class _Cur:
    def __init__(self, rows):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.sqls = []

    def execute(self, sql, params=None):
        self.sqls.append((sql, params))
        return _Cur(self._rows)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDateCol:
    def test_prefers_trade_date(self):
        from src.data_sync.pool_reconcile import date_col_of
        assert date_col_of(["ts_code", "trade_date"]) == "trade_date"

    def test_falls_back_to_other_date(self):
        from src.data_sync.pool_reconcile import date_col_of
        assert date_col_of(["ts_code", "end_date"]) == "end_date"

    def test_none_when_no_date(self):
        from src.data_sync.pool_reconcile import date_col_of
        assert date_col_of(["ts_code", "name"]) is None


class TestStaleOrder:
    def test_oldest_first_and_unseen_first(self):
        """无记录（空串）排最前；其余按日期升序——这是「永不饿死」的机制本体。"""
        from src.data_sync import pool_reconcile as R
        metas = {"t1": {"pg_table": "tab1", "pk": ["symbol", "trade_date"]}}
        with patch.object(R, "last_dates", return_value={"A": "20261020", "B": "20260901"}):
            order = R.stale_order(metas, ["A", "B", "C"])
        assert order[0][0] == "C", order   # 无记录=最陈旧，先跑
        assert order[1][0] == "B" and order[2][0] == "A"

    def test_single_table_order_is_global(self):
        from src.data_sync import pool_reconcile as R
        metas = {
            "income": {"pg_table": "financial_income", "pk": ["symbol", "end_date"]},
            "cyq_chips": {"pg_table": "cyq_chips", "pk": ["symbol", "trade_date"]},
        }
        seen = {"financial_income": {"A": "20250101"}, "cyq_chips": {"A": "20261020"}}
        # 新语义（代码审 P0-3）：陈旧度＝与**本表自身最新日期**的差 ⇒ 两表都停在各自 tmax 时
        # 陈旧度同为 0，按族权重 A 优先（income 属 D 族，cyq_chips 属 A 族）。
        with patch.object(R, "last_dates", side_effect=lambda t, c, sc, s=None: seen[t]):
            order = R.stale_order(metas, ["A"])
        assert order[0] == ("A", "cyq_chips"), order

    def test_behind_own_table_max_goes_first(self):
        """同一表内：落后于本表 tmax 的标的必须先跑（这才是「补最旧」的本体）。"""
        from src.data_sync import pool_reconcile as R
        metas = {"income": {"pg_table": "income", "pk": ["ts_code", "ann_date", "end_date"]}}
        seen = {"income": {"OLD": "20240101", "NEW": "20260930"}}
        with patch.object(R, "last_dates", side_effect=lambda t, c, sc, s=None: seen[t]):
            order = R.stale_order(metas, ["NEW", "OLD"])
        assert order[0] == ("OLD", "income"), order


class TestFamilyA:
    def test_lagging_symbols_flagged(self):
        from src.data_sync import pool_reconcile as R
        metas = {"cyq_chips": {"pg_table": "cyq_chips", "pk": ["ts_code", "trade_date"]}}
        # 当日无行情标的不判（停牌/退市/次新）⇒ 这里显式放行两个标的
        with patch.object(R, "_traded_on", return_value={"A.SHSE", "B.SZSE"}), \
                patch.object(R, "last_dates", return_value={"A.SHSE": "20261020", "B.SZSE": "20261010"}):
            out = R.check_family_a(metas, ["A.SHSE", "B.SZSE"], "20261020")
        assert len(out) == 1 and "1/2" in out[0] and "B" in out[0]

    def test_all_fresh_no_finding(self):
        from src.data_sync import pool_reconcile as R
        metas = {"cyq_chips": {"pg_table": "cyq_chips", "pk": ["symbol", "trade_date"]}}
        with patch.object(R, "last_dates", return_value={"A": "20261020"}):
            assert R.check_family_a(metas, ["A"], "20261020") == []

    def test_missing_expected_skips(self):
        from src.data_sync import pool_reconcile as R
        assert R.check_family_a({}, ["A"], None) == []


class TestFamilyB:
    def test_zero_rows_on_trade_day_flagged(self):
        from src.data_sync import pool_reconcile as R
        conn = _Conn([(0,)])
        with patch.object(R, "get_conn", return_value=conn), \
                patch("src.data_sync.pool_data._nearest_trade_day", return_value="20261021"):
            out = R.check_family_b()
        assert any("无任何行" in x for x in out), out

    def test_has_rows_passes(self):
        from src.data_sync import pool_reconcile as R
        with patch.object(R, "get_conn", return_value=_Conn([(42,)])), \
                patch("src.data_sync.pool_data._nearest_trade_day", return_value="20261021"):
            assert R.check_family_b() == []


class TestFamilyC:
    def _rows(self, ts):
        return [("astock_list", "success", ts)]

    def test_stale_flagged(self):
        from src.data_sync import pool_reconcile as R
        old = datetime.now(timezone.utc) - timedelta(days=10)
        with patch.object(R, "get_conn", return_value=_Conn(self._rows(old))):
            out = R.check_family_c()
        assert out and "10 天" in out[0], out

    def test_fresh_passes(self):
        from src.data_sync import pool_reconcile as R
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        with patch.object(R, "get_conn", return_value=_Conn(self._rows(recent))):
            assert R.check_family_c() == []

    def test_never_synced_flagged(self):
        from src.data_sync import pool_reconcile as R
        with patch.object(R, "get_conn", return_value=_Conn([("trade_cal", "idle", None)])):
            out = R.check_family_c()
        assert out and "从未" in out[0]


class TestFamilyD:
    def test_streak_increments_then_alerts(self):
        from src.data_sync import pool_reconcile as R
        conn = _Conn([("5",)])          # 上轮 streak=5
        with patch.object(R, "get_conn", return_value=conn):
            findings = R.check_family_d({"income": 0}, [])
        assert findings and "连续 6 轮" in findings[0], findings
        assert any("INSERT INTO system_config" in s[0] for s in conn.sqls)

    def test_rows_reset_streak(self):
        from src.data_sync import pool_reconcile as R
        conn = _Conn([("5",)])
        with patch.object(R, "get_conn", return_value=conn):
            assert R.check_family_d({"income": 7}, []) == []
        ins = [s for s in conn.sqls if "INSERT INTO system_config" in s[0]]
        assert ins and ins[-1][1][1] == "0"

    def test_error_round_resets(self):
        from src.data_sync import pool_reconcile as R
        conn = _Conn([("5",)])
        with patch.object(R, "get_conn", return_value=conn):
            assert R.check_family_d({"income": 0}, ["income: ReadTimeout"]) == []


class TestReconcileIsolation:
    def test_family_exception_isolated(self):
        """任一族抛错 ⇒ 变成一条 finding，不炸其它族、不炸同步。"""
        from src.data_sync import pool_reconcile as R
        with patch.object(R, "check_family_a", side_effect=RuntimeError("boom")), \
                patch.object(R, "check_family_b", return_value=["b-finding"]), \
                patch.object(R, "check_family_c", return_value=[]), \
                patch.object(R, "check_family_d", return_value=[]), \
                patch("src.data_sync.pool_data._nearest_trade_day", return_value="20261021"):
            res = R.reconcile({}, ["A"])
        assert any("a 族判定异常" in x for x in res["a"])
        assert "b-finding" in res["findings"]


class _Lock:
    acquired = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestRunPoolSyncOrdering:
    def test_uses_stale_order_and_reconciles(self):
        """接线钉：run_pool_sync 必须按 stale_order 给的下标顺序拉取，且非 timebox 轮跑对账。"""
        from src.data_sync import pool_data as P
        seen = []

        class _Ad:
            provider = "tushare"

            def fetch(self, req):
                seen.append((req.symbols[0] if req.symbols else "", metrics["table"]))
                return SimpleNamespace(rows=[])

        metrics = {}

        def _fake_request(table, ts_code, kind, window):
            metrics["table"] = table
            return SimpleNamespace(symbols=[ts_code], kind=kind)

        with patch.object(P, "_get_pool_ts_codes", return_value=["A", "B"]), \
                patch.object(P, "_pool_meta", return_value={"kind": "featured_daily", "pg_table": "cyq_chips", "pk": ["symbol", "trade_date"]}), \
                patch.object(P, "_pool_request", side_effect=_fake_request), \
                patch.object(P, "_window_of", return_value=(None, None)), \
                patch.object(P, "_sink_frame", return_value=1), \
                patch.object(P, "_advance_cursors"), \
                patch.object(P, "_load_cursors", return_value={}), \
                patch.object(P, "_get_kline_adapter", return_value=_Ad()), \
                patch.object(P, "_get_rate_ds", return_value=None), \
                patch.object(P, "rate_limit_context"), \
                patch.object(P, "_provider_of", return_value="tushare"), \
                patch.object(P, "SyncLock", return_value=_Lock()), \
                patch("src.data_sync.pool_reconcile.stale_order", return_value=[("B", "cyq_chips"), ("A", "cyq_chips")]) as so, \
                patch("src.data_sync.pool_reconcile.reconcile", return_value={"findings": []}) as rec:
            out = P.run_pool_sync({"provider": "tushare"}, timebox_s=60)
        assert so.called and rec.called
        assert [s[0] for s in seen] == ["B", "A"], seen
        assert out["saved"] == 2

class TestRegressionOfReviewP0:
    """代码审 3 个 P0 的判别钉（原测试全 mock 掩盖了列名/范围错误）。"""

    def test_last_dates_sql_uses_ts_code_not_symbol(self):
        """P0-1：池表列名是 ts_code（0046）——SQL 里出现 symbol 就是错的。"""
        from src.data_sync import pool_reconcile as R
        conn = _Conn([("600000.SH", "20261020")])
        with patch.object(R, "get_conn", return_value=conn):
            R.last_dates("cyq_chips", "trade_date", "ts_code", ["600000.SH"])
        sql = conn.sqls[0][0]
        assert "ts_code" in sql
        assert "symbol" not in sql, sql

    def test_symbol_col_derived_from_pk(self):
        from src.data_sync.pool_reconcile import symbol_col_of
        assert symbol_col_of(["ts_code", "trade_date"]) == "ts_code"
        assert symbol_col_of(["symbol", "ts"]) == "symbol"

    def test_family_b_is_explicit_six(self):
        """P0-2：B 族只能含 6 张按日快照表——cyq_perf/forecast 进 B 必假报。"""
        from src.data_sync.pool_reconcile import FAMILY_B
        tables = [t for t, *_rest in FAMILY_B]   # 批 147 三元组（+lag）——首个元素恒表名
        assert len(tables) == 6, tables
        assert "cyq_perf" not in tables and "forecast" not in tables

    def test_incomplete_round_skips_family_d(self):
        """P1：时间盒截断轮不 bump D 族 streak（否则整轮 0 行判定失真）。"""
        from src.data_sync import pool_reconcile as R
        with patch.object(R, "check_family_a", return_value=[]), \
                patch.object(R, "check_family_b", return_value=[]), \
                patch.object(R, "check_family_c", return_value=[]), \
                patch.object(R, "check_family_d", return_value=[]) as cd, \
                patch("src.data_sync.pool_data._nearest_trade_day", return_value="20261021"):
            R.reconcile({}, ["A"], complete=False)
        assert not cd.called

    def test_family_c_uses_real_sync_ids(self):
        """P1：concept 的实际 sync_config id 是 concept_sync（写错永远查不到）。"""
        from src.data_sync.pool_reconcile import FAMILY_C
        assert "concept_sync" in FAMILY_C and "concept" not in FAMILY_C
    def test_stale_order_derives_symbol_column(self):
        """stale_order 必须把 pk 派生的符号列传下去（否则又退回 symbol 错列）。"""
        from src.data_sync import pool_reconcile as R
        metas = {"cyq_chips": {"pg_table": "cyq_chips", "pk": ["ts_code", "trade_date"]}}
        got = {}

        def _spy(t, c, sc, s=None):
            got["sc"] = sc
            return {"600000.SH": "20261020"}

        with patch.object(R, "last_dates", side_effect=_spy):
            R.stale_order(metas, ["600000.SH"])
        assert got.get("sc") == "ts_code", got