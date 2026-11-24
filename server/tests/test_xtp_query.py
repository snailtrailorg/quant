"""XTP 在途委托查询测试（无真 SDK）——钉参数顺序与登录门（2026-11-24 SEGV 事故）。"""
from src.providers.xtp.query import query_unfinished_orders


class _Fake:
    def __init__(self, session_id=7):
        self.session_id = session_id
        self.login_status = session_id > 0
        self.calls = []

    # 真实签名：QueryUnfinishedOrdersEx(uint64 session_id, int request_id)
    def queryUnfinishedOrdersEx(self, sid, rid):
        self.calls.append((sid, rid))
        self.onQueryOrderEx({"order_xtp_id": "111", "ticker": "600000"}, {}, rid, False, sid)
        self.onQueryOrderEx({"order_xtp_id": "222", "ticker": "000001"}, {}, rid, True, sid)
        return 0


def test_query_uses_session_first_and_collects_until_last():
    f = _Fake(session_id=7)
    out = query_unfinished_orders(f, timeout_s=0.2)
    assert set(out) == {"111", "222"}
    sid, rid = f.calls[0]
    assert sid == 7 and isinstance(rid, int), "session 必须是第 1 参（传反会越界 SEGV）"
    assert hasattr(f, "onQueryOrderEx"), "回调应永久安装，不随查询删除"


def test_query_blocked_before_login():
    """session_id=0（未登录）绝不能调 XTP——无效会话查槽位会越界崩溃。"""
    f = _Fake(session_id=0)
    assert query_unfinished_orders(f, timeout_s=0.05) == {}
    assert f.calls == []


def test_late_callback_for_unknown_reqid_is_ignored():
    f = _Fake(session_id=7)
    query_unfinished_orders(f, timeout_s=0.05)
    # 迟到回调 reqid 已不在 pending：不得抛、不得污染
    f.onQueryOrderEx({"order_xtp_id": "999"}, {}, 123456, True, 7)


def test_query_unfinished_without_api_returns_empty():
    assert query_unfinished_orders(None) == {}
