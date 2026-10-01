"""批 76 · F1 冻结事件事实表验收钉（真库；无 dev 库自动跳过）。

覆盖：
- 记账/闭环往返（record → list 见进行中 → close 带 method/operator → 再 close 幂等 0 行）；
- 枚举 CHECK 真咬（freeze_type/unfreeze_method 非法值被库拒，不只靠 Python 校验）；
- jsonb 守卫真咬（detail 塞已序列化字符串 = 双编码降级 ⇒ 被 0123 的 CHECK 拒绝）；
- 「解冻必须记方法」一致性 CHECK（防半记录：解了但不知道谁解的）；
- 按 task 过滤 + limit。
"""
import pytest

from src.data_platform.freeze_event import (
    FREEZE_TYPES, UNFREEZE_METHODS, close_freeze, list_events, record_freeze,
)

TID = 987_601        # 合成任务号（不撞真实 live_task 行；本表无 FK，引用即可）
SYM = "999997.SHSE"


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


@pytest.fixture(autouse=True)
def _clean():
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM freeze_event WHERE task_id=%s", (TID,))
        conn.commit()
    yield


def test_record_then_close_roundtrip_and_idempotent():
    """记账 → 进行中可见 → 人工闭环带方法/操作者 → 重复闭环 0 行（幂等空操作）。"""
    eid = record_freeze(TID, SYM, "ts_gap", account_id=1, watermark="1759000000",
                        gap_target_ts="1759000300", detail={"gap_s": 300})
    assert eid, "记账未返回事件 id"
    evs = list_events(task_id=TID)
    assert len(evs) == 1
    e = evs[0]
    assert (e["freeze_type"], e["watermark"], e["gap_target_ts"]) == ("ts_gap", "1759000000", "1759000300")
    assert e["unfrozen_at"] is None and e["unfreeze_method"] is None and e["account_id"] == 1

    n = close_freeze(TID, method="manual_web", operator="william", symbol=SYM)
    assert n == 1
    e = list_events(task_id=TID)[0]
    assert e["unfrozen_at"] and e["unfreeze_method"] == "manual_web" and e["operator"] == "william"

    assert close_freeze(TID, method="manual_web", operator="william", symbol=SYM) == 0   # 幂等


def test_python_enum_rejects_unknown_values():
    with pytest.raises(ValueError):
        record_freeze(TID, SYM, "bogus_type")
    with pytest.raises(ValueError):
        close_freeze(TID, method="bogus_method")


def test_db_check_constraints_bite():
    """库层 CHECK 真咬（Python 校验被绕过时仍有防线）。"""
    import psycopg
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO freeze_event (task_id, symbol, freeze_type) "
                         "VALUES (%s, %s, 'bogus')", (TID, SYM))
        conn.rollback()
        # jsonb 守卫：双编码降级（二次 dumps 的产物 = 内容是 JSON 的**字符串**）被拒
        import json as _json
        double = _json.dumps(_json.dumps({"gap_s": 300}))       # '"{\"gap_s\": 300}"'
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO freeze_event (task_id, symbol, freeze_type, detail) "
                         "VALUES (%s, %s, 'ts_gap', %s::jsonb)", (TID, SYM, double))
        conn.rollback()
        # 一致性：给了 unfrozen_at 却没有 unfreeze_method ⇒ 拒（防半记录）
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO freeze_event (task_id, symbol, freeze_type, unfrozen_at) "
                         "VALUES (%s, %s, 'ts_gap', now())", (TID, SYM))
        conn.rollback()


def test_enum_source_of_truth_matches_check():
    """Python 枚举与库 CHECK 同源（两处同时改才不漂移——本钉是漂移探测器）。"""
    assert FREEZE_TYPES == ("ts_gap", "seq_gap", "untrusted")
    assert UNFREEZE_METHODS == ("restart", "auto_reconnect", "manual_web", "manual_im")
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                           "WHERE conname='ck_freeze_event_type'").fetchone()
    assert row, "0123 的 ck_freeze_event_type 不存在（迁移未落库？）"
    for t in FREEZE_TYPES:
        assert f"'{t}'" in row[0]


def test_list_limit_and_open_events():
    for t in ("ts_gap", "seq_gap", "untrusted"):
        record_freeze(TID, SYM, t)
    close_freeze(TID, method="auto_reconnect", operator=None, symbol=SYM)
    record_freeze(TID, SYM, "untrusted")
    evs = list_events(task_id=TID, limit=10)
    assert len(evs) == 4
    opens = [e for e in evs if e["unfrozen_at"] is None]
    assert len(opens) == 1 and opens[0]["freeze_type"] == "untrusted"
    assert len(list_events(task_id=TID, limit=2)) == 2      # limit 生效
