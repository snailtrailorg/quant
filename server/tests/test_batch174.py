"""批 174 守门：风控金额截断按手取整 + 网关拒因可审计（全 mock）。"""
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from vnpy.trader.constant import Status

from src.data_platform.specs import InstrumentSpec
from src.providers.xtp import td as xtp_td
from src.risk_control.risk import RiskControl


def _rc():
    rc = object.__new__(RiskControl)
    rc._rules = {"etf_conv": {"max_single_amount": 100000, "single_position_pct": 1.0}}
    rc._symbol_exposure = lambda sym, aid: (0.0, 1e12)
    return rc


def _run(order, category):
    with patch.object(InstrumentSpec, "of", return_value=NS(category=category)):
        return _rc()._check_etf_conv(order, 1)


def test_clip_stock_rounds_to_100():
    # int(100000/180.26)=554 → 500（原值 554 非整百必被交易所拒 Invalid security quantity）
    d = _run({"symbol": "001232.XSHE", "action": "BUY", "volume": 900, "price": 180.26}, "stock")
    assert d.approved and d.adjusted["volume"] == 500


def test_clip_listed_fund_rounds_to_100():
    d = _run({"symbol": "510010.SSE", "action": "BUY", "volume": 900, "price": 180.26}, "listed_fund")
    assert d.approved and d.adjusted["volume"] == 500


def test_clip_convertible_rounds_to_10():
    # int(100000/120)=833 → 830（可转债 10 张一手）
    d = _run({"symbol": "110000.SSE", "action": "BUY", "volume": 900, "price": 120.0}, "convertible")
    assert d.approved and d.adjusted["volume"] == 830


def test_clip_below_one_lot_rejects():
    d = _run({"symbol": "600000.SSE", "action": "BUY", "volume": 100, "price": 2000.0}, "stock")
    assert not d.approved and "不足 1 手" in d.reason


def test_flat_or_reduce_not_clipped():
    d = _run({"symbol": "600000.SSE", "action": "SELL", "volume": 900, "price": 200.0,
              "reason": "emergency_close_all"}, "stock")
    assert d.approved and d.adjusted is None


def test_fmt_error_keeps_code_and_msg():
    assert xtp_td._fmt_error("交易委托失败", {"error_id": 10000, "error_msg": ""}) == "交易委托失败，代码：10000，信息："
    assert (xtp_td._fmt_error("交易委托失败", {"error_id": 1, "error_msg": "Invalid security quantity."})
            == "交易委托失败，代码：1，信息：Invalid security quantity.")
    assert xtp_td._fmt_error("x", None) == "x"
    assert xtp_td._fmt_error("x", {}) == "x"


def test_provider_on_order_does_not_write_db():
    """分层纪律：插件 _on_order 只缓存，不写业务库（order_log 落库归编排层）。"""
    port = object.__new__(xtp_td.XtpExecutionPort)
    port._lock = threading.Lock()
    port._orders = {}
    conn = MagicMock()
    conn.__enter__.return_value = conn
    with patch("src.data_platform.db.get_conn", return_value=conn):
        port._on_order(NS(data=NS(vt_orderid="vt-1", status=Status.REJECTED)))
    assert not conn.execute.called


def test_pop_last_reject_consumes_within_window():
    xtp_td._LAST_REJECT.update({"ts": time.time(), "detail": "交易委托失败，代码：10000，信息："})
    assert xtp_td.pop_last_reject().startswith("交易委托失败")
    assert xtp_td.pop_last_reject() == ""       # 已消费
    xtp_td._LAST_REJECT.update({"ts": 0.0, "detail": "old"})
    assert xtp_td.pop_last_reject() == ""       # 陈旧 → 不返回


def test_write_order_status_writes_reject_error():
    """编排层落拒因：write_order_status 在 rejected 时按传入 error 写 order_log.error。"""
    from src.quant_common.domain import OrderStatus
    from src.strategy_runner.trading import write_order_status
    d = NS(status=OrderStatus.REJECTED, vt_orderid="vt-1", symbol="600000.SSE", client_id="")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = (42,)
    with patch("src.data_platform.db.get_conn", return_value=conn):
        write_order_status(d, MagicMock(), "sid", "600000.SSE",
                           error="交易委托失败，代码：10000，信息：")
    upd = [c for c in conn.execute.call_args_list if "SET" in c.args[0]]
    assert any("error=%s" in c.args[0] and "交易委托失败" in str(c.args[1]) for c in upd)


def test_sell_matches_shse_sse_alias():
    """持仓 vnpy 口径 .SSE、委托项目口径 .XSHG → 双写候选匹配（否则沪市手动卖恒拒 NO_SELLABLE）。"""
    from src.strategy_runner import trading
    a = MagicMock()
    a.send_order.return_value = "vt-1"
    a.get_vt_orderid.return_value = "vt-1"
    a.query_position.return_value = [NS(symbol="600036.SSE", volume=800, frozen=0)]
    dec = NS(approved=True, adjusted=None)
    body = {"symbol": "600036.XSHG", "action": "SELL", "volume": 500, "price": 44.2,
            "client_order_id": "mo-x"}
    with patch("src.risk_control.risk.RiskControl.get") as get, \
         patch.object(trading, "manual_order_open", return_value=42), \
         patch.object(trading, "_close_order_mark"):
        get.return_value.check_order.return_value = dec
        out = trading.submit_manual_order(a, 1, "admin", "r1", body)
    assert out["accepted"] is True and out["volume"] == 500
