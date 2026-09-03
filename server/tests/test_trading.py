"""trading.py 共享件测试（批 4a）：九单元单测——direct 与 hub worker 双模式单源后的语义锁。

收编：TestFlushPositions/TestAccountBaseline/真连接冒烟（原 test_position_snapshot，挂点改接）；
补：write_trade_log / snapshot_cycle / halt_edge_cancel / recalc_hook / stop_due / reconcile_orders
（frozen_allows/buy_ok_check 语义断言留 test_hub_arch，仅 import 改挂）。
mock 方式：MagicMock adapter + patch src.data_platform.db.get_conn（不连真库）。
"""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import src.data_platform.db as db
from src.strategy_runner import trading
from vnpy.trader.constant import Direction, Status


def _pos(symbol="600000.SHSE", volume=1000, direction="long", frozen=200, yd=800):
    return SimpleNamespace(symbol=symbol, volume=volume, avg_price=9.05, pnl=50.0,
                           direction=direction, frozen=frozen, yd_volume=yd)


def _acct(balance=1_000_000.0, frozen=0.0):
    return SimpleNamespace(balance=balance, frozen=frozen)


def _trade(direction=Direction.LONG, vt_orderid="x.1", vt_tradeid="t1", symbol="600000.SHSE"):
    return SimpleNamespace(direction=direction, vt_orderid=vt_orderid, vt_tradeid=vt_tradeid,
                           datetime="2026-08-27 09:31:00", symbol=symbol, volume=100, price=9.05)


def _adapter():
    adapter = MagicMock()
    adapter._lock = threading.Lock()
    adapter._vt2cid = {}
    return adapter


class TestFlushPositions:
    """ST2 持仓真相批（N 审 v2 语义；批 4a 自 test_position_snapshot 收编，断言原样）。"""

    def _run(self, positions, account_id="253191001822", task_id=8):
        adapter = MagicMock()
        adapter.query_position.return_value = positions
        conn = MagicMock()
        conn.__enter__.return_value = conn
        with patch.object(db, "get_conn", return_value=conn):
            trading._flush_positions(adapter, account_id, task_id)
        return conn

    def test_overwrite_write_single_transaction(self):
        """N-v2：单事务 DELETE 该账户 + INSERT 批 + upsert refresh（一次 commit）。"""
        conn = self._run([_pos()])
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("DELETE FROM position_snapshot" in s for s in sqls)
        assert any("ON CONFLICT (account_id)" in s for s in sqls)   # refresh upsert
        # O-F1：executemany 走 cursor（池化连接无此方法——F 审同款坑的回归锁）
        cur = conn.cursor.return_value.__enter__.return_value
        cur.executemany.assert_called_once()
        assert "ON CONFLICT (account_id, symbol, direction)" in cur.executemany.call_args.args[0]
        conn.commit.assert_called_once()   # 单事务

    def test_empty_batch_clears_state_and_writes_heartbeat(self):
        """N-F1：清仓 0 行——DELETE 仍执行（表空=空仓），refresh rows=0（空批可表示）。"""
        conn = self._run([])
        conn.cursor.assert_not_called()   # 空批不 INSERT
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("DELETE FROM position_snapshot" in s for s in sqls)
        upsert = [c for c in conn.execute.call_args_list if "ON CONFLICT (account_id)" in c.args[0]]
        assert upsert and upsert[0].args[1] == ("253191001822", 0, "8", 0, "8")

    def test_account_id_none_becomes_default(self):
        conn = self._run([], account_id=None)
        del_call = [c for c in conn.execute.call_args_list if "DELETE" in c.args[0]][0]
        assert del_call.args[1] == ("default",)

    def test_failure_does_not_raise(self):
        """写批失败仅日志（不阻断主循环）。"""
        adapter = MagicMock()
        adapter.query_position.side_effect = Exception("TD 断线")
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            trading._flush_positions(adapter, "x", 1)   # 不抛即过

    def test_short_rows_written_not_filtered(self):
        """N-S3：两融 Short 行如实写（不过滤），端点侧再选向。"""
        conn = self._run([_pos(), _pos(symbol="600000.SHSE", volume=100, direction="short")])
        cur = conn.cursor.return_value.__enter__.return_value
        rows = cur.executemany.call_args.args[1]
        assert len(rows) == 2 and rows[1][2] == "short"


class TestWriteTradeLog:
    """统一 RETURNING 版（知情差异⑤：worker 侧同步获得成交入库观测日志）。"""

    def _conn(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        return conn

    def test_returning_insert_and_commit(self):
        conn = self._conn()
        adapter = _adapter()
        conn.execute.return_value.fetchone.return_value = None   # F-50：cid 与 vt_orderid 均无匹配
        with patch.object(db, "get_conn", return_value=conn):
            trading.write_trade_log(_trade(), adapter, "s1", "600000.SHSE")
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("ON CONFLICT (trade_ref) DO NOTHING RETURNING id" in s for s in sqls)
        insert = [c for c in conn.execute.call_args_list if "INSERT INTO trade_log" in c.args[0]][0]
        assert insert.args[1][1] == "s1"          # strategy_id 兜底=传入 sid
        assert insert.args[1][2] is None          # 无 cid → order_db_id None
        conn.commit.assert_called_once()

    def test_cid_resolves_order_db_id_and_strategy(self):
        conn = self._conn()
        adapter = _adapter()
        adapter._vt2cid = {"x.1": "CID-9"}
        conn.execute.return_value.fetchone.return_value = (42, "s-owner")
        with patch.object(db, "get_conn", return_value=conn):
            trading.write_trade_log(_trade(), adapter, "s1", "600000.SHSE")
        lookup = conn.execute.call_args_list[0]
        assert "FROM order_log WHERE client_order_id=%s" in lookup.args[0]
        assert lookup.args[1] == ("CID-9",)
        insert = [c for c in conn.execute.call_args_list if "INSERT INTO trade_log" in c.args[0]][0]
        assert insert.args[1][1] == "s-owner" and insert.args[1][2] == 42

    def test_vt_orderid_fallback_resolves_order(self):
        """F-50：重启后 _vt2cid 空（cid=None），用 vt_orderid 反查 order_log。"""
        conn = self._conn()
        adapter = _adapter()   # _vt2cid 空
        conn.execute.return_value.fetchone.return_value = (99, "s-owner")
        with patch.object(db, "get_conn", return_value=conn):
            trading.write_trade_log(_trade(), adapter, "s1", "600000.SHSE")
        lookup = conn.execute.call_args_list[0]
        assert "FROM order_log WHERE vt_orderid=%s" in lookup.args[0]
        assert lookup.args[1] == ("x.1",)
        insert = [c for c in conn.execute.call_args_list if "INSERT INTO trade_log" in c.args[0]][0]
        assert insert.args[1][1] == "s-owner" and insert.args[1][2] == 99

    def test_sell_direction_mapped(self):
        conn = self._conn()
        with patch.object(db, "get_conn", return_value=conn):
            trading.write_trade_log(_trade(direction=Direction.SHORT), _adapter(), "s1", "X.SHSE")
        insert = [c for c in conn.execute.call_args_list if "INSERT INTO trade_log" in c.args[0]][0]
        assert insert.args[1][4] == "SELL"

    def test_db_error_never_raises(self):
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            trading.write_trade_log(_trade(), _adapter(), "s1", "X.SHSE")   # 不抛即过


class TestAccountBaseline:
    """#10 口径修正：initial_capital 列=账户基线净值（4a 缓存改调用方持有 dict，语义等价）。"""

    def test_baseline_from_first_snapshot(self):
        """有历史快照 -> 基线=首条 total_value（而非传入当前值/live_task 配置）。"""
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (1_000_000_000,)
        with patch.object(db, "get_conn", return_value=conn):
            v = trading._account_baseline_capital(5_000_000, {"baseline": None})
        assert v == 1_000_000_000

    def test_no_history_uses_current_value(self):
        """无历史（首次跟踪）-> 以当前查询值为基线。"""
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = None
        with patch.object(db, "get_conn", return_value=conn):
            v = trading._account_baseline_capital(5_000_000, {"baseline": None})
        assert v == 5_000_000

    def test_baseline_cached_across_calls(self):
        """调用方持有缓存：第二次调用不再查库（基线不随运行漂移）。"""
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (1_000_000_000,)
        cache = {"baseline": None}
        with patch.object(db, "get_conn", return_value=conn):
            v1 = trading._account_baseline_capital(5_000_000, cache)
            v2 = trading._account_baseline_capital(9_000_000, cache)
        assert v1 == v2 == 1_000_000_000
        assert conn.execute.call_count == 1

    def test_db_error_falls_back_to_current(self):
        """查库失败 -> 以当前值为基线（不抛，快照写入不阻断）。"""
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            v = trading._account_baseline_capital(5_000_000, {"baseline": None})
        assert v == 5_000_000


class TestSnapshotCycle:
    """direct 形态（知情差异②：含 available_cash、单事务；worker 落库多一列，无消费者受扰）。"""

    def test_empty_accounts_skips_no_fake_values(self):
        """SB1（F-34）：TD 断线 query_account=[] 绝不写假值——不碰库不写持仓批。"""
        adapter = MagicMock()
        adapter.query_account.return_value = []
        with patch.object(db, "get_conn") as gc, \
             patch.object(trading, "_flush_positions") as fp:
            trading.snapshot_cycle(adapter, "acct", 8, {"baseline": None})
        gc.assert_not_called()
        fp.assert_not_called()

    def test_single_txn_with_available_cash_and_flush(self):
        """单事务：当日基准 SELECT + INSERT（含 available_cash）+ 同拍持仓批 + 一次 commit。"""
        adapter = MagicMock()
        adapter.query_account.return_value = [_acct(balance=100.0, frozen=30.0), _acct(balance=50.0)]
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (120.0,)   # 今日首条快照
        with patch.object(db, "get_conn", return_value=conn), \
             patch.object(trading, "_account_baseline_capital", return_value=1.0) as base, \
             patch.object(trading, "_flush_positions"):   # 持仓批自持连接/自有事务，单测隔离（挂点另有锁）
            trading.snapshot_cycle(adapter, "acct", 8, {"baseline": None})
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("SELECT total_value FROM account_snapshot WHERE ts::date=%s" in s for s in sqls)
        insert = [c for c in conn.execute.call_args_list
                  if "INSERT INTO account_snapshot" in c.args[0]][0]
        assert insert.args[1] == (150.0, 30.0, 1.0, 120.0)   # total/daily_pnl/基线/available(balance-frozen)
        base.assert_called_once()
        conn.commit.assert_called_once()

    def test_flush_wired_with_same_account_and_task(self):
        """ST2：同拍写持仓批，account_id/tid 原样透传（挂点契约）。"""
        adapter = MagicMock()
        adapter.query_account.return_value = [_acct()]
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = None
        with patch.object(db, "get_conn", return_value=conn), \
             patch.object(trading, "_account_baseline_capital", return_value=1.0), \
             patch.object(trading, "_flush_positions") as fp:
            trading.snapshot_cycle(adapter, "acct-9", 12, {"baseline": None})
        fp.assert_called_once_with(adapter, "acct-9", 12)

    def test_db_error_never_raises(self):
        adapter = MagicMock()
        adapter.query_account.return_value = [_acct()]
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            trading.snapshot_cycle(adapter, "a", 1, {"baseline": None})   # 不抛即过


class TestHaltEdgeCancel:
    """SB2 熔断沿撤在场单（F-41；文案统一 direct 版——知情差异④）。"""

    @staticmethod
    def _rc(halted: bool):
        stub = MagicMock()
        stub.is_halted.return_value = halted
        return patch("src.risk_control.risk.RiskControl.get", return_value=stub)

    def _order(self, status, vt_orderid="x.1"):
        return SimpleNamespace(status=status, vt_orderid=vt_orderid)

    def test_edge_triggers_cancel_of_working_orders_only(self):
        adapter = MagicMock()
        adapter.query_orders.return_value = [
            self._order(Status.NOTTRADED, "x.1"), self._order(Status.ALLTRADED, "x.2"),
            self._order(Status.PARTTRADED, "x.3")]
        state = {"was": False}
        with self._rc(True), patch.object(trading, "_alert") as al:
            trading.halt_edge_cancel(adapter, state, "s1")
        cancelled = [c.args[0] for c in adapter.cancel_order.call_args_list]
        assert cancelled == ["x.1", "x.3"]
        assert state["was"] is True
        al.assert_called_once()   # direct 版文案告警
        assert "已自动撤销在场委托" in al.call_args.args[0]

    def test_no_edge_no_cancel(self):
        adapter = MagicMock()
        state = {"was": True}
        with self._rc(True):
            trading.halt_edge_cancel(adapter, state, "s1")
        adapter.cancel_order.assert_not_called()

    def test_risk_query_failure_keeps_previous_state(self):
        """Valkey 不可达 → 保持上一状态（check_order 侧已保守拒单），不误触沿。"""
        adapter = MagicMock()
        state = {"was": False}
        with patch("src.risk_control.risk.RiskControl.get", side_effect=Exception("Valkey down")):
            trading.halt_edge_cancel(adapter, state, "s1")
        adapter.cancel_order.assert_not_called()
        assert state["was"] is False

    def test_cancel_failure_does_not_break_loop(self):
        adapter = MagicMock()
        adapter.query_orders.return_value = [self._order(Status.NOTTRADED)]
        adapter.cancel_order.side_effect = Exception("TD 断")
        state = {"was": False}
        with self._rc(True), patch.object(trading, "_alert"):
            trading.halt_edge_cancel(adapter, state, "s1")   # 单笔撤单失败仅日志
        assert state["was"] is True


class TestRecalcHook:
    """因子重算/热重载钩子（rewarm 注入：direct=PG 重填，worker=PG+流回放）。"""

    def test_trigger_reloads_factors_and_rewarms_without_deleting(self):
        trading._recalc_seen = None   # F-55：模块级 last_seen，测试隔离
        r = MagicMock()
        r.get.return_value = "1"
        rewarm = MagicMock()
        history = [1, 2, 3]
        with patch("src.strategy_framework.factor.load_factors_from_db") as lf:
            trading.recalc_hook(r, rewarm, history)
        lf.assert_called_once()
        rewarm.assert_called_once()
        r.delete.assert_not_called()   # F-55：不删全局键（多 worker 各记 last_seen）

    def test_no_trigger_noop(self):
        trading._recalc_seen = None
        r = MagicMock()
        r.get.return_value = None
        rewarm = MagicMock()
        with patch("src.strategy_framework.factor.load_factors_from_db") as lf:
            trading.recalc_hook(r, rewarm, [])
        lf.assert_not_called()
        rewarm.assert_not_called()
        r.delete.assert_not_called()

    def test_factor_reload_failure_still_rewarms(self):
        trading._recalc_seen = None
        r = MagicMock()
        r.get.return_value = "1"
        rewarm = MagicMock()
        with patch("src.strategy_framework.factor.load_factors_from_db",
                   side_effect=Exception("DB 抖动")):
            trading.recalc_hook(r, rewarm, [])   # 因子加载失败被吞，重灌照走
        rewarm.assert_called_once()

    def test_redis_error_never_raises(self):
        r = MagicMock()
        r.get.side_effect = Exception("Valkey down")
        trading.recalc_hook(r, MagicMock(), [])   # 不抛即过


class TestStopDue:
    """P4-3 停止条件（tid/sid 双态单源；节奏在调用侧——worker 5s / direct 60s，知情差异③）。"""

    def _conn(self, fetchone):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = fetchone
        return conn

    def test_tid_stopped(self):
        with patch.object(db, "get_conn", return_value=self._conn(("stopped",))):
            assert trading.stop_due(8, "s1") is True

    def test_tid_running(self):
        with patch.object(db, "get_conn", return_value=self._conn(("running",))):
            assert trading.stop_due(8, "s1") is False

    def test_sid_disabled_when_tid_none(self):
        with patch.object(db, "get_conn", return_value=self._conn((False,))):
            assert trading.stop_due(None, "s1") is True

    def test_tid_takes_precedence_no_strategy_query(self):
        """新架构只查 live_task（2026-08-17 踩坑回归锁：不误查旧架构字段）。"""
        conn = self._conn(("running",))
        with patch.object(db, "get_conn", return_value=conn):
            trading.stop_due(8, "s1")
        assert "live_task" in conn.execute.call_args.args[0]
        assert conn.execute.call_count == 1

    def test_db_error_returns_false(self):
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            assert trading.stop_due(8, "s1") is False   # 不退出，下轮再查


class TestReconcileOrders:
    """SC2 runner 超集（知情差异①：worker 由只告警在场委托升级为三件套，知情接受）。"""

    def test_working_orders_alerted_not_cancelled(self):
        adapter = MagicMock()
        adapter.query_orders.return_value = [
            SimpleNamespace(status=Status.NOTTRADED, symbol="600000.SHSE", direction=Direction.LONG,
                            volume=100, price=9.0)]
        adapter.query_trades.return_value = []
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = []
        with patch.object(db, "get_conn", return_value=conn), \
             patch.object(trading, "_alert") as al, \
             patch.object(trading, "write_trade_log") as wt:
            trading.reconcile_orders(adapter, "s1", "600000.SHSE")
        adapter.cancel_order.assert_not_called()   # v1：只告警不自动撤
        al.assert_called_once()
        assert "启动对账发现 1 笔在场委托" in al.call_args.args[0]
        wt.assert_not_called()

    def test_trades_backfilled_via_write_trade_log(self):
        adapter = MagicMock()
        adapter.query_orders.return_value = []
        adapter.query_trades.return_value = [_trade(), _trade(vt_tradeid="t2")]
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = []
        with patch.object(db, "get_conn", return_value=conn), \
             patch.object(trading, "_alert"), \
             patch.object(trading, "write_trade_log") as wt:
            trading.reconcile_orders(adapter, "s1", "600000.SHSE")
        assert wt.call_count == 2
        wt.assert_called_with(adapter.query_trades.return_value[1], adapter, "s1", "600000.SHSE")

    def test_wal_orphans_alerted(self):
        adapter = MagicMock()
        adapter.query_orders.return_value = []
        adapter.query_trades.return_value = []
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = [(7, "600000.SHSE", "BUY", 100)]
        with patch.object(db, "get_conn", return_value=conn), \
             patch.object(trading, "_alert") as al:
            trading.reconcile_orders(adapter, "s1", "600000.SHSE")
        wal_alert = [c for c in al.call_args_list if "WAL 残留" in c.args[0]]
        assert wal_alert and "1 笔" in wal_alert[0].args[0]

    def test_query_failure_never_raises(self):
        adapter = MagicMock()
        adapter.query_orders.side_effect = Exception("TD 断")
        trading.reconcile_orders(adapter, "s1", "X.SHSE")   # 不抛即过


class TestRealConnectionSmoke:
    """O 必查 7③：真 psycopg 连接冒烟（本地 dev DB；不可达则跳过）——4a 收编改挂 trading。"""

    def test_flush_roundtrip_on_real_db(self):
        import pytest
        try:
            with db.get_conn() as conn:
                conn.execute("SELECT 1 FROM position_snapshot LIMIT 1")
                conn.commit()
        except Exception:
            pytest.skip("本地 PG 不可达或 0043 未迁移")

        adapter = MagicMock()
        adapter.query_position.return_value = [_pos()]
        trading._flush_positions(adapter, "smoke_test_acct", 99)
        try:
            with db.get_conn() as conn:
                cur = conn.execute("SELECT symbol, volume, direction FROM position_snapshot "
                                   "WHERE account_id=%s", ("smoke_test_acct",))
                rows = cur.fetchall()
                cur = conn.execute("SELECT rows, task_id FROM position_refresh "
                                   "WHERE account_id=%s", ("smoke_test_acct",))
                ref = cur.fetchone()
            assert rows and rows[0][0] == "600000.SHSE" and rows[0][2] == "long"
            assert ref and ref[0] == 1 and ref[1] == "99"
            # 空批覆盖（N-F1 全链）：再跑空批 → 行清空 refresh rows=0
            adapter2 = MagicMock()
            adapter2.query_position.return_value = []
            trading._flush_positions(adapter2, "smoke_test_acct", 99)
            with db.get_conn() as conn:
                cur = conn.execute("SELECT count(*) FROM position_snapshot WHERE account_id=%s",
                                   ("smoke_test_acct",))
                assert cur.fetchone()[0] == 0, "空批必须清掉旧行（当前状态表语义）"
                cur = conn.execute("SELECT rows FROM position_refresh WHERE account_id=%s",
                                   ("smoke_test_acct",))
                assert cur.fetchone()[0] == 0
        finally:
            with db.get_conn() as conn:
                conn.execute("DELETE FROM position_snapshot WHERE account_id=%s", ("smoke_test_acct",))
                conn.execute("DELETE FROM position_refresh WHERE account_id=%s", ("smoke_test_acct",))
                conn.commit()
