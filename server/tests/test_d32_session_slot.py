"""批 172d P3 · 会话号段租约守门（真库）。"""
from __future__ import annotations

import pytest

from src.data_platform.db import get_conn
from src.strategy_runner.session_slot import (
    SessionBusy,
    SessionConfigError,
    SessionLost,
    SessionSlotAllocator,
    SessionUnavailable,
)

ACC = 989910


def _db_up() -> bool:
    try:
        with get_conn() as c:
            c.execute("SELECT 1 FROM provider_session_slot LIMIT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="真库不可用")


@pytest.fixture(autouse=True)
def _clean():
    with get_conn() as c:
        c.execute("DELETE FROM provider_session_slot WHERE account_id=%s", (ACC,))
        c.commit()
    yield
    with get_conn() as c:
        c.execute("DELETE FROM provider_session_slot WHERE account_id=%s", (ACC,))
        c.commit()


def _acquire(holder="h1", tid=1, role="td", terminal=None):
    return SessionSlotAllocator.acquire(ACC, "xtp", role, tid, boot_epoch=1000,
                                        window_open=True, terminal_id=terminal, holder=holder)


def test_acquire_and_release():
    s = _acquire()
    assert s.terminal_id == 2 and s.order_prefix == "t1:e1000:"
    with get_conn() as c:
        assert c.execute("SELECT holder FROM provider_session_slot WHERE account_id=%s",
                         (ACC,)).fetchone()[0] == "h1"
    SessionSlotAllocator.release(s)
    with get_conn() as c:
        assert c.execute("SELECT count(*) FROM provider_session_slot WHERE account_id=%s",
                         (ACC,)).fetchone()[0] == 0


def test_second_holder_busy():
    _acquire("h1")
    with pytest.raises(SessionBusy):
        _acquire("h2")


def test_stale_heartbeat_can_be_taken():
    _acquire("h1")
    with get_conn() as c:
        c.execute("UPDATE provider_session_slot SET heartbeat=now()-interval '60 seconds' "
                  "WHERE account_id=%s", (ACC,))
        c.commit()
    s = _acquire("h2")
    assert s.holder == "h2"


def test_renew_lost_when_taken_over():
    s = _acquire("h1")
    with get_conn() as c:
        c.execute("UPDATE provider_session_slot SET holder='h2', heartbeat=now() "
                  "WHERE account_id=%s", (ACC,))
        c.commit()
    with pytest.raises(SessionLost):
        SessionSlotAllocator.renew(s)


def test_old_holder_release_does_not_delete_new_owner():
    s = _acquire("h1")
    with get_conn() as c:
        c.execute("UPDATE provider_session_slot SET holder='h2' WHERE account_id=%s", (ACC,))
        c.commit()
    SessionSlotAllocator.release(s)
    with get_conn() as c:
        assert c.execute("SELECT holder FROM provider_session_slot WHERE account_id=%s",
                         (ACC,)).fetchone()[0] == "h2"


def test_terminal_config_errors():
    with pytest.raises(SessionConfigError):
        _acquire(role="md", terminal=None)     # MD 必须显式预留号


def test_terminal_out_of_range_explicit():
    with pytest.raises(SessionConfigError):
        _acquire(role="td", terminal=1)
    with pytest.raises(SessionConfigError):
        _acquire(role="md", terminal=5)


def test_db_unavailable_does_not_fail_open(monkeypatch):
    import src.data_platform.db as db
    monkeypatch.setattr(db, "get_conn", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(SessionUnavailable):
        _acquire("h9")
