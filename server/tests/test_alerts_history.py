"""系统告警页数据层（health_events）单测：活动/历史/清理 SQL 形状与投影。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.data_platform import health_events as H


def _ctx(rows=None, rowcount=0):
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    cur.rowcount = rowcount
    conn = MagicMock()
    conn.execute.return_value = cur
    ctx = MagicMock()
    ctx.__enter__ = lambda s: conn
    ctx.__exit__ = lambda *a: False
    return ctx, conn, cur


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as c:
            c.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _db_up(), reason="真库行为（无 dev 库自动跳过）")
def test_resolved_collapses_episode_and_keeps_active():
    """真库：warn→crit→recovery 只出一条历史；活动告警不被历史/清除误删。"""
    import uuid
    from src.data_platform.db import get_conn
    from src.data_platform.health_events import active_alerts, clear_resolved, resolved_alerts
    tag = "ut-" + uuid.uuid4().hex[:8]
    try:
        with get_conn() as c:
            c.execute("INSERT INTO health_event (rule_id,component,severity,detail,ts) VALUES (%s,%s,'warning','w',now()-interval '3 min')", ("r-"+tag, tag))
            c.execute("INSERT INTO health_event (rule_id,component,severity,detail,ts) VALUES (%s,%s,'critical','c',now()-interval '2 min')", ("r-"+tag, tag))
            c.execute("INSERT INTO health_event (rule_id,component,severity,detail,ts) VALUES (%s,%s,'recovery','',now()-interval '1 min')", ("r-"+tag, tag))
            c.execute("INSERT INTO health_event (rule_id,component,severity,detail) VALUES (%s,%s,'critical','active')", ("r2-"+tag, tag))
            c.commit()
        hist = [x for x in resolved_alerts(500) if x["rule_id"] == "r-"+tag]
        assert len(hist) == 1 and hist[0]["severity"] == "warning" and hist[0]["resolved_ts"]
        assert any(x["rule_id"] == "r2-"+tag for x in active_alerts())
        clear_resolved()
        assert not [x for x in resolved_alerts(500) if x["rule_id"] == "r-"+tag]
        assert any(x["rule_id"] == "r2-"+tag for x in active_alerts())   # 活动行保留
    finally:
        with get_conn() as c:
            c.execute("DELETE FROM health_event WHERE component=%s", (tag,))
            c.commit()


def test_active_alerts_projection():
    ctx, conn, _ = _ctx([("t1", "hub_hb_lost", "md-hub:md-xtp", "critical", "d", None)])
    with patch.object(H, "get_conn", lambda: ctx):
        out = H.active_alerts()
    assert out[0]["rule_id"] == "hub_hb_lost" and out[0]["resolved_ts"] is None
    sql = conn.execute.call_args[0][0]
    assert "DISTINCT ON" in sql and "recovery" in sql


def test_resolved_alerts_has_resolved_ts():
    ctx, conn, _ = _ctx([("t1", "dep_down", "postgres", "critical", "d", "t2")])
    with patch.object(H, "get_conn", lambda: ctx):
        out = H.resolved_alerts(50)
    assert out[0]["resolved_ts"] == "t2"
    assert "LATERAL" in conn.execute.call_args[0][0]


def test_clear_resolved_returns_rowcount():
    ctx, conn, _ = _ctx(rowcount=7)
    with patch.object(H, "get_conn", lambda: ctx):
        n = H.clear_resolved()
    assert n == 7 and conn.commit.called
