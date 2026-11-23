"""XTP 在途委托查询薄适配测试（无真 SDK）。"""
from src.providers.xtp.query import query_unfinished_orders


class _Fake:
    session_id = 7

    def __init__(self):
        self.calls = []

    def queryUnfinishedOrdersEx(self, rid, sid):
        self.calls.append((rid, sid))
        self.onQueryOrderEx({"order_xtp_id": "111", "ticker": "600000"}, {}, rid, False, sid)
        self.onQueryOrderEx({"order_xtp_id": "222", "ticker": "000001"}, {}, rid, True, sid)
        return 0


def test_query_unfinished_collects_until_last_and_restores_callback():
    f = _Fake()
    out = query_unfinished_orders(f, timeout_s=0.2)
    assert set(out) == {"111", "222"}
    assert f.calls and f.calls[0][1] == 7
    assert not hasattr(f, "onQueryOrderEx")


def test_query_unfinished_without_api_returns_empty():
    assert query_unfinished_orders(None) == {}
