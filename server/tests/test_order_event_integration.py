"""order_event 事件账真库集成守门（评审 P0：mock 掩盖「缺 commit 事件账恒空」）。

本地 dev 库 quant_189_full 停在 0192（无 order_event），故测试自建同构表；
数据用唯一 req_id/client_order_id/trade_ref 隔离，finally 清理，不污染其它测试。
无 dev 库时自动跳过。
"""
from __future__ import annotations

import time
import uuid

import pytest

from src.data_platform.db import get_conn


def _db_up() -> bool:
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def _event_table():
    if not _db_up():
        pytest.skip("无 dev 库（真库集成守门跳过）")
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_event (
              id bigserial PRIMARY KEY,
              order_id bigint REFERENCES order_log(id) ON DELETE SET NULL,
              client_order_id text, req_id text, ts timestamptz NOT NULL DEFAULT now(),
              stage text NOT NULL, status text, source text NOT NULL,
              detail jsonb, error text,
              CONSTRAINT ck_order_event_detail_object
                CHECK (detail IS NULL OR jsonb_typeof(detail) = 'object')
            )
        """)
        # 本地库可能已有无守卫的 order_event（自建或旧跑遗留）→ 补齐守卫，与 0196 一致
        conn.execute("""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_order_event_detail_object') THEN
                ALTER TABLE order_event ADD CONSTRAINT ck_order_event_detail_object
                  CHECK (detail IS NULL OR jsonb_typeof(detail) = 'object');
              END IF;
            END $$
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_event_order ON order_event (order_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_event_req ON order_event (req_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_event_cid ON order_event (client_order_id, ts)")
        conn.commit()
    yield


def _cleanup(*, req=None, oid=None):
    with get_conn() as conn:
        if req:
            conn.execute("DELETE FROM order_event WHERE req_id=%s", (req,))
        if oid:
            conn.execute("DELETE FROM order_event WHERE order_id=%s", (oid,))
            conn.execute("DELETE FROM trade_log WHERE order_id=%s", (oid,))
            conn.execute("DELETE FROM order_log WHERE id=%s", (oid,))
        conn.commit()


def test_migrations_bind_execute_use_text_not_bare_string():
    """SQLAlchemy 2.x：op.get_bind().execute(裸字符串) 真库上产必炸（0196 实测）。

    静态守门全仓 migrations：get_bind().execute( 后必须接 text(/sa.text(。
    """
    import ast
    from pathlib import Path
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    bad = []
    for path in versions.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            # 匹配 get_bind().execute(...)
            if (isinstance(f, ast.Attribute) and f.attr == "execute"
                    and isinstance(f.value, ast.Call)
                    and isinstance(f.value.func, ast.Attribute)
                    and f.value.func.attr == "get_bind"):
                if not node.args:
                    continue
                arg = node.args[0]
                ok = (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
                      and arg.func.id == "text") or \
                     (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                      and arg.func.attr == "text")
                if not ok:
                    bad.append(f"{path.name}:{node.lineno}")
    assert not bad, f"get_bind().execute 传裸字符串（SQLAlchemy 2.x 真库会炸）: {bad}"


@pytest.mark.skipif(not _db_up(), reason="真库集成守门（无 dev 库跳过）")
def test_record_event_really_persists(_event_table):
    """record_order_event 写后真能查到——钉「with get_conn 退出回滚」的 P0。"""
    from src.strategy_runner.trading import record_order_event
    req = "itest-ev-" + uuid.uuid4().hex[:10]
    try:
        record_order_event(stage="send", source="td_exec", req_id=req,
                           status="submitted", detail={"symbol": "600000.XSHG"})
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT stage, status, detail->>'symbol' FROM order_event WHERE req_id=%s",
                (req,)).fetchall()
        assert len(rows) == 1 and rows[0][0] == "send" and rows[0][2] == "600000.XSHG"
    finally:
        _cleanup(req=req)


@pytest.mark.skipif(not _db_up(), reason="真库集成守门（无 dev 库跳过）")
def test_reconcile_really_marks_unknown(_event_table):
    """reconcile_stale_orders 真把超时单标 unknown 并落 timeout 事件。"""
    from unittest.mock import MagicMock
    from src.strategy_runner import trading
    cid = "itest-rc-" + uuid.uuid4().hex[:10]
    with get_conn() as conn:
        oid = conn.execute(
            "INSERT INTO order_log (ts, strategy_id, symbol, action, volume, price, status, "
            "account_code, client_order_id, vt_orderid) "
            "VALUES (now(), 'itest', '600000.XSHG', 'BUY', 100, 10.0, 'submitted', "
            "'td-xtp', %s, %s) RETURNING id", (cid, "XTP.ITEST" + uuid.uuid4().hex[:8])).fetchone()[0]
        conn.commit()
    try:
        record_order_event = trading.record_order_event
        record_order_event(stage="send", source="td_exec", order_id=oid, status="submitted")
        adapter = MagicMock()
        adapter.query_orders.return_value = []
        out = trading.reconcile_stale_orders(adapter, "td-xtp", threshold_s=0,
                                             now=time.time() + 100000, broker_query=lambda: {})
        with get_conn() as conn:
            st = conn.execute("SELECT status FROM order_log WHERE id=%s", (oid,)).fetchone()[0]
            evs = [r[0] for r in conn.execute(
                "SELECT stage FROM order_event WHERE order_id=%s", (oid,)).fetchall()]
        assert st == "unknown" and "timeout" in evs
        assert out["stale"] >= 1
    finally:
        _cleanup(oid=oid)


def _order_state(status, vt):
    from src.quant_common.domain import Direction, OrderState
    return OrderState(orderid="", vt_orderid=vt, symbol="600000.XSHG",
                      direction=Direction.LONG, status=status, volume=100,
                      traded=0, price=10.0)


def _insert_order(status, vt):
    cid = "itest-fault-" + uuid.uuid4().hex[:10]
    with get_conn() as conn:
        oid = conn.execute(
            "INSERT INTO order_log (ts, strategy_id, symbol, action, volume, price, status, "
            "account_code, client_order_id, vt_orderid) "
            "VALUES (now(), 'itest-fault', '600000.XSHG', 'BUY', 100, 10.0, %s, "
            "'td-xtp', %s, %s) RETURNING id", (status, cid, vt)).fetchone()[0]
        conn.commit()
    return oid


def _status_and_events(oid):
    with get_conn() as conn:
        st = conn.execute("SELECT status FROM order_log WHERE id=%s", (oid,)).fetchone()[0]
        evs = [r[0] for r in conn.execute(
            "SELECT stage||':'||COALESCE(status,'') FROM order_event WHERE order_id=%s ORDER BY id",
            (oid,)).fetchall()]
    return st, evs


class TestCallbackFaultInjection:
    """异步回调乱序/重复/迟到打在真库订单行上，验状态机收敛（D34 §8）。"""

    @pytest.mark.skipif(not _db_up(), reason="真库集成守门（无 dev 库跳过）")
    def test_duplicate_all_traded_writes_once(self, _event_table):
        from src.quant_common.domain import OrderStatus
        from src.strategy_runner.trading import write_order_status
        from unittest.mock import MagicMock
        vt = "XTP.ITEST" + uuid.uuid4().hex[:8]
        oid = _insert_order("submitted", vt)
        try:
            d = _order_state(OrderStatus.ALL_TRADED, vt)
            write_order_status(d, MagicMock(), "", "")
            write_order_status(d, MagicMock(), "", "")   # 重复回调
            st, evs = _status_and_events(oid)
            assert st == "all_traded"
            assert evs.count("broker:all_traded") == 1, "重复回调写了重复事件"
        finally:
            _cleanup(oid=oid)

    @pytest.mark.skipif(not _db_up(), reason="真库集成守门（无 dev 库跳过）")
    def test_late_all_traded_after_cancel_keeps_cancel(self, _event_table):
        """先撤单终态，迟到全成回调不得冲掉（只进不退）。"""
        from src.quant_common.domain import OrderStatus
        from src.strategy_runner.trading import write_order_status
        from unittest.mock import MagicMock
        vt = "XTP.ITEST" + uuid.uuid4().hex[:8]
        oid = _insert_order("submitted", vt)
        try:
            write_order_status(_order_state(OrderStatus.CANCELLED, vt), MagicMock(), "", "")
            write_order_status(_order_state(OrderStatus.ALL_TRADED, vt), MagicMock(), "", "")  # 迟到
            st, evs = _status_and_events(oid)
            assert st == "cancelled", f"迟到全成冲掉撤单终态: {st}"
            assert "broker:all_traded" not in evs, "被挡的终态仍写了事件"
        finally:
            _cleanup(oid=oid)

    @pytest.mark.skipif(not _db_up(), reason="真库集成守门（无 dev 库跳过）")
    def test_late_partial_after_all_traded_keeps_all_traded(self, _event_table):
        """先全成，迟到部分成交片段不得回退。"""
        from src.quant_common.domain import OrderStatus
        from src.strategy_runner.trading import write_order_status
        from unittest.mock import MagicMock
        vt = "XTP.ITEST" + uuid.uuid4().hex[:8]
        oid = _insert_order("submitted", vt)
        try:
            write_order_status(_order_state(OrderStatus.ALL_TRADED, vt), MagicMock(), "", "")
            write_order_status(_order_state(OrderStatus.PARTIAL, vt), MagicMock(), "", "")  # 迟到片段
            st, _ = _status_and_events(oid)
            assert st == "all_traded"
        finally:
            _cleanup(oid=oid)

    @pytest.mark.skipif(not _db_up(), reason="真库集成守门（无 dev 库跳过）")
    def test_unknown_corrected_by_late_terminal(self, _event_table):
        """unknown 不在终态集，迟到券商终态可纠正（否则永久 unknown）。"""
        from src.quant_common.domain import OrderStatus
        from src.strategy_runner.trading import write_order_status
        from unittest.mock import MagicMock
        vt = "XTP.ITEST" + uuid.uuid4().hex[:8]
        oid = _insert_order("unknown", vt)
        try:
            write_order_status(_order_state(OrderStatus.REJECTED, vt), MagicMock(), "", "")
            st, evs = _status_and_events(oid)
            assert st == "rejected"
            assert "broker:rejected" in evs
        finally:
            _cleanup(oid=oid)


@pytest.mark.skipif(not _db_up(), reason="真库集成守门（无 dev 库跳过）")
def test_resolve_unknown_really_advances(_event_table):
    """resolve_unknown_orders 真按 trade_log 成交量推进 all_traded。"""
    from src.strategy_runner.trading import resolve_unknown_orders, record_order_event
    cid = "itest-rs-" + uuid.uuid4().hex[:10]
    tref = "itest-tr-" + uuid.uuid4().hex[:10]
    with get_conn() as conn:
        oid = conn.execute(
            "INSERT INTO order_log (ts, strategy_id, symbol, action, volume, price, status, "
            "account_code, client_order_id) "
            "VALUES (now(), 'itest', '600000.XSHG', 'BUY', 100, 10.0, 'unknown', "
            "'td-xtp', %s) RETURNING id", (cid,)).fetchone()[0]
        conn.execute(
            "INSERT INTO trade_log (ts, order_id, symbol, action, volume, price, trade_ref, account_code) "
            "VALUES (now(), %s, '600000.XSHG', 'BUY', 100, 10.0, %s, 'td-xtp')", (oid, tref))
        conn.commit()
    try:
        out = resolve_unknown_orders("td-xtp")
        with get_conn() as conn:
            st = conn.execute("SELECT status FROM order_log WHERE id=%s", (oid,)).fetchone()[0]
        assert st == "all_traded" and out["resolved"] >= 1
    finally:
        _cleanup(oid=oid)
