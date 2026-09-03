"""SC 订单批次测试（SC1/SC2/SC3，2026-08-17 稳定性检查 F-4/F-27/F-28/F-8/#46）。

覆盖：
- F-4 WAL 时序：先记账(submitting)后发单；记账失败 fail-closed 不发单
- F-27 假委托号：adapter 返回 None → send_failed
- F-28 源头拦截：0 量/0 价信号直接丢弃，不进 adapter
- SC3 max_trades_per_day 计数护栏
"""
from unittest.mock import MagicMock, patch

import pytest

from src.strategy_framework.strategy import Strategy, StrategyConfig, Signal, Action
from src.risk_control.risk import RiskControl


class FakeAdapter:
    def __init__(self, ret="c1", exc=None):
        self.calls = []
        self.ret = ret
        self.exc = exc

    def send_order(self, order):
        self.calls.append(order)
        if self.exc:
            raise self.exc
        return self.ret

    def get_vt_orderid(self, client_id):
        return None   # 对齐 ExecutionAdapter 基类默认（F-50 加）


@pytest.fixture
def strat():
    cfg = StrategyConfig(id="t-sc", name="t", type="astock_analysis",
                         symbol="600000.SHSE", adapter="xtp")
    s = Strategy(cfg, None)
    return s


@pytest.fixture
def rc_ok():
    saved = RiskControl._instance
    c = RiskControl()
    RiskControl._instance = saved
    c.is_halted = lambda: False
    c.is_live_trading_allowed = lambda market: True
    c._get_global_state = lambda a: type("S", (), {"available": True, "total_drawdown": 0.0, "daily_loss": 0.0})()
    return c


def _sig(volume=100, price=10.0, action=Action.BUY):
    return Signal(action=action, score=0.5, symbol="600000.SHSE",
                  volume=volume, price=price, reason="test")


class TestSourceGuard:
    def test_zero_volume_dropped(self, strat, rc_ok):
        """F-28：0 量信号不进 adapter 不进风控。"""
        adapter = FakeAdapter()
        strat.adapter = adapter
        with patch.object(RiskControl, "get", return_value=rc_ok):
            strat.place_order(_sig(volume=0))
        assert adapter.calls == []

    def test_zero_price_dropped(self, strat, rc_ok):
        adapter = FakeAdapter()
        strat.adapter = adapter
        with patch.object(RiskControl, "get", return_value=rc_ok):
            strat.place_order(_sig(price=0))
        assert adapter.calls == []


class TestWALOrdering:
    def test_log_before_send_then_submitted(self, strat, rc_ok):
        """F-4：_log_signal_order 先于 send_order；成功后状态流转 submitted+client_order_id。"""
        adapter = FakeAdapter(ret="c42")
        strat.adapter = adapter
        events = []
        with patch.object(RiskControl, "get", return_value=rc_ok):
            with patch.object(strat, "_log_signal_order", side_effect=lambda *a, **k: events.append("log") or (1, 7)) as mlog:
                with patch.object(strat, "_update_order_status",
                                  side_effect=lambda *a, **k: events.append(("update", a[1], k.get("client_order_id")))) as mupd:
                    strat.place_order(_sig())
        assert events[0] == "log", "必须先记账后发单"
        assert adapter.calls, "应发单"
        mlog.assert_called_once()
        assert mupd.call_count == 1
        assert events[-1] == ("update", "submitted", "c42")

    def test_wal_failure_blocks_send(self, strat, rc_ok):
        """F-4 fail-closed：记账失败 → 不发单。"""
        adapter = FakeAdapter()
        strat.adapter = adapter
        with patch.object(RiskControl, "get", return_value=rc_ok):
            with patch.object(strat, "_log_signal_order", return_value=(None, None)):
                strat.place_order(_sig())
        assert adapter.calls == []

    def test_fake_orderid_marks_send_failed(self, strat, rc_ok):
        """F-27：adapter 返回 None → send_failed，不记 submitted。"""
        adapter = FakeAdapter(ret=None)
        strat.adapter = adapter
        statuses = []
        with patch.object(RiskControl, "get", return_value=rc_ok):
            with patch.object(strat, "_log_signal_order", return_value=(1, 9)):
                with patch.object(strat, "_update_order_status",
                                  side_effect=lambda oid, st, **k: statuses.append(st)):
                    strat.place_order(_sig())
        assert statuses == ["send_failed"]

    def test_send_exception_marks_send_failed_and_raises(self, strat, rc_ok):
        adapter = FakeAdapter(exc=RuntimeError("网络断"))
        strat.adapter = adapter
        statuses = []
        with patch.object(RiskControl, "get", return_value=rc_ok):
            with patch.object(strat, "_log_signal_order", return_value=(1, 9)):
                with patch.object(strat, "_update_order_status",
                                  side_effect=lambda oid, st, **k: statuses.append(st)):
                    with pytest.raises(RuntimeError):
                        strat.place_order(_sig())
        assert statuses == ["send_failed"]


class TestMaxTradesPerDay:
    def test_at_limit_rejected(self, rc_ok):
        """SC3：当日单数达上限拒新单。"""
        import src.risk_control.risk as risk_mod

        class CountConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                class Cur:
                    def fetchone(self):
                        return (20,)
                return Cur()

        with patch.object(risk_mod, "get_conn", lambda: CountConn()):
            d = rc_ok._check_etf_conv({"volume": 100, "price": 10.0, "strategy_id": "s1"})
        assert not d.approved and "上限" in d.reason

    def test_count_failure_fail_closed(self, rc_ok):
        import src.risk_control.risk as risk_mod

        class BoomConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                raise RuntimeError("PG 挂")

        with patch.object(risk_mod, "get_conn", lambda: BoomConn()):
            d = rc_ok._check_etf_conv({"volume": 100, "price": 10.0, "strategy_id": "s1"})
        assert not d.approved and d.severity == "critical"
