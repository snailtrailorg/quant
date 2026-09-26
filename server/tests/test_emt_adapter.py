"""批 63 P4：EmtAdapter 测试（vnpy 形状合成/8 值状态映射/分帧聚合/重登/短号/market 对调/EOD）。

双先例：fake 绑定模块（test_emq_md_gateway）+ __new__ 绕构造（test_xtp_adapter_query）。
"""
import os
import types
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.strategy_framework.adapters import EmtAdapter, Order


# --- fake 绑定模块（不依赖 .so——CI/无编译环境可跑） ---

class _FakeOrderInsertInfo:
    def __init__(self):
        self.ticker = ""
        self.market = None
        self.price = 0.0
        self.quantity = 0
        self.price_type = None
        self.side = 0
        self.order_client_id = 0
        self.business_type = 0


class _FakeRaw:
    """EMT 原生回报形状（属性直给——回调帧内拷贝在 adapter 侧即时读）。"""
    def __init__(self, **kw):
        self.order_emt_id = kw.get("order_emt_id", 1001)
        self.order_client_id = kw.get("order_client_id", 0)
        self.ticker = kw.get("ticker", "600000")
        self.market = kw.get("market", 2)          # 2=SH_A
        self.price = kw.get("price", 10.0)
        self.quantity = kw.get("quantity", 100)
        self.qty_traded = kw.get("qty_traded", 0)
        self.qty_left = kw.get("qty_left", 100)
        self.order_status = kw.get("order_status", None)
        self.side = kw.get("side", 1)
        self.price_type = kw.get("price_type", 1)
        self.trade_time = kw.get("trade_time", 0)
        self.trade_amount = kw.get("trade_amount", 0.0)
        self.report_index = kw.get("report_index", 1)
        self.exec_id = kw.get("exec_id", "e1")
        self.order_local_id = kw.get("order_local_id", "L1")


def _fake_mod():
    """构造与 bind_trader 同名的 fake 模块（枚举/常量对齐真实值）。"""
    import enum
    Status = enum.Enum("EMT_ORDER_STATUS_TYPE", {
        "INIT": 0, "ALLTRADED": 1, "PARTTRADEDQUEUEING": 2, "PARTTRADEDNOTQUEUEING": 3,
        "NOTRADEQUEUEING": 4, "CANCELED": 5, "REJECTED": 6, "UNKNOWN": 7})
    Market = enum.Enum("EMT_MARKET_TYPE", {"INIT": 0, "SZ_A": 1, "SH_A": 2, "BJ_A": 5})
    Price = enum.Enum("EMT_PRICE_TYPE", {
        "LIMIT": 1, "BEST5_OR_LIMIT": 2, "BEST_OR_CANCEL": 3, "BEST5_OR_CANCEL": 4,
        "FORWARD_BEST": 5, "REVERSE_BEST_LIMIT": 6})
    Resume = enum.Enum("EMT_TE_RESUME_TYPE", {"RESTART": 0, "RESUME": 1, "QUICK": 2})
    m = types.SimpleNamespace(
        EMT_ORDER_STATUS_TYPE=Status, EMT_MARKET_TYPE=Market, EMT_PRICE_TYPE=Price,
        EMT_TE_RESUME_TYPE=Resume,
        EMT_SIDE_BUY=1, EMT_SIDE_SELL=2, EMT_BUSINESS_TYPE_CASH=0,
        EMTOrderInsertInfo=_FakeOrderInsertInfo)
    return m


@pytest.fixture
def fake_binding(monkeypatch):
    m = _fake_mod()
    monkeypatch.setattr("src.strategy_framework.adapters._load_emt_binding", lambda: m)
    return m


def _bare_adapter(**kw):
    """__new__ 绕构造 + 手塞依赖面（XTP 测试先例）。"""
    a = EmtAdapter.__new__(EmtAdapter)
    a._gateway = None
    a._event_engine = None
    a._order_prefix = "t7:e1:"
    a._td_client_id = 26
    a._api = MagicMock()
    a._api.GetTradingDay.return_value = "20260926"   # 同日——防 MagicMock 返回值意外触发 EOD 重建
    a._spi = object()
    a._session_id = 555
    a._trading_day = "20260926"
    a._orders = {}
    a._trades = {}
    a._positions = {}
    a._accounts = {}
    a._cid2vt = {}
    a._vt2cid = {}
    a._cid2sn = {}
    a._sn_seq = 0
    a._cid_seq = 0
    a._request_seq = 0
    a._pending = {}
    a._lock = __import__("threading").Lock()
    a._last_setting = {"td_host": "h", "td_port": 1, "account": "a", "password": "p"}
    for k, v in kw.items():
        setattr(a, k, v)
    return a


# --- vnpy 形状合成（A/B 双盲 P0 立法钉） ---

def test_order_event_synthesizes_vnpy_shape(fake_binding):
    """OnOrderEvent→vnpy OrderData（Status 枚举/vt_orderid=EMT.{id}/reference=client_id 还原）。"""
    a = _bare_adapter()
    a._cid2sn["t7:e1:c1"] = 42
    a._on_order_raw(_FakeRaw(order_client_id=42, order_status=fake_binding.EMT_ORDER_STATUS_TYPE.NOTRADEQUEUEING),
                    None, 555)
    od = a._orders["EMT.1001"]
    from vnpy.trader.constant import Status, Direction
    assert od.status == Status.NOTTRADED and od.direction == Direction.LONG
    assert od.vt_orderid == "EMT.1001" and od.symbol == "600000" and od.volume == 100.0
    assert od.reference == "t7:e1:c1"           # 短号反查还原


def test_status_all_eight_values(fake_binding):
    """8 值枚举全映射（部撤=CANCELLED 终态/UNKNOWN 保守 SUBMITTING）。"""
    a = _bare_adapter()
    from vnpy.trader.constant import Status
    expect = {"INIT": Status.SUBMITTING, "ALLTRADED": Status.ALLTRADED,
              "PARTTRADEDQUEUEING": Status.PARTTRADED, "PARTTRADEDNOTQUEUEING": Status.CANCELLED,
              "NOTRADEQUEUEING": Status.NOTTRADED, "CANCELED": Status.CANCELLED,
              "REJECTED": Status.REJECTED, "UNKNOWN": Status.SUBMITTING}
    for name, want in expect.items():
        got = a._status_of(fake_binding.EMT_ORDER_STATUS_TYPE[name])
        assert got == want, f"{name}: {got} != {want}"


def test_trade_event_synthesizes_vnpy_shape(fake_binding):
    a = _bare_adapter()
    a._on_trade_raw(_FakeRaw(side=2, price=10.5, quantity=50), 555)
    td = a._trades["EMT.1001.e1"]
    from vnpy.trader.constant import Direction
    assert td.direction == Direction.SHORT and td.price == 10.5 and td.volume == 50.0


def test_account_synthesizes_balance_frozen(fake_binding):
    """query_account→vnpy AccountData（balance/frozen/available——snapshot_cycle 消费面）。"""
    a = _bare_adapter()

    class _Asset:
        total_asset = 100000.0
        buying_power = 80000.0

    def _query(sid, rid):
        a._frame(rid, _Asset(), is_last=True)

    a._api.QueryAsset.side_effect = _query
    accounts = a.query_account()
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.balance == 100000.0 and acc.frozen == 20000.0 and acc.available == 80000.0


# --- 下单/撤单 ---

def test_send_order_market_type_and_market_swap(fake_binding):
    """市价钉 BEST5_OR_CANCEL；market 后缀→EMT 枚举对调映射（SHSE→SH_A=2）。"""
    a = _bare_adapter()
    a._api.InsertOrder.return_value = 9001
    cid = a.send_order(Order(symbol="600000.SHSE", action="BUY", volume=100,
                             price=0.0, order_type="market"))
    assert cid == "t7:e1:c1"
    req = a._api.InsertOrder.call_args.args[0]
    assert req.market.value == 2 and a._api.InsertOrder.call_args.args[1] == 555
    assert req.price_type.value == 4   # BEST5_OR_CANCEL
    assert a.get_vt_orderid("t7:e1:c1") == "EMT.9001"


def test_send_order_fail_returns_none(fake_binding):
    a = _bare_adapter()
    a._api.InsertOrder.return_value = 0
    assert a.send_order(Order(symbol="600000.SHSE", action="BUY", volume=1, price=1.0)) is None


def test_send_order_session_dead_fail_fast(fake_binding):
    a = _bare_adapter()
    a._session_id = 0
    with pytest.raises(RuntimeError, match="session"):
        a.send_order(Order(symbol="600000.SHSE", action="BUY", volume=1, price=1.0))


# --- 分帧聚合 ---

def test_query_position_frames_and_mapping(fake_binding):
    a = _bare_adapter()

    class _Pos:
        ticker = "510300"
        market = 1          # SZ_A → .SZSE
        total_qty = 1000
        sellable_qty = 400  # T+1：frozen=600
        avg_price = 3.5
        unrealized_pnl = 0.0
        yesterday_position = 1000

    def _query(ticker, sid, rid):
        a._frame(rid, _Pos(), is_last=False)
        a._frame(rid, _Pos(), is_last=True)   # 分帧两拍到齐

    a._api.QueryPosition.side_effect = _query
    out = a.query_position()
    assert len(out) == 2
    p = out[0]
    assert p.symbol == "510300.SZSE" and p.volume == 1000 and p.frozen == 600
    assert p.yd_volume == 1000 and p.avg_price == 3.5


# --- EOD / 重登 ---

def test_eod_rebuild_sequence(fake_binding):
    """交易日变更→清单例→Release 旧句柄→connect（remember_login 参数重建）。"""
    a = _bare_adapter()
    old_api = a._api
    EmtAdapter._API_SINGLETON = a._api
    a._api.GetTradingDay.return_value = "20260929"   # 变更日
    with patch.object(EmtAdapter, "connect") as mock_connect:
        a._check_eod()
        mock_connect.assert_called_once_with(a._last_setting)
    assert EmtAdapter._API_SINGLETON is None and a._session_id == 0 and a._api is None
    old_api.Release.assert_called_once()   # 单例句柄重建前显式释放（否则 Create 返同句柄=重建失效）


def test_relogin_sequence(fake_binding):
    """重登=Logout(old)→Subscribe(RESTART)→Login（SDK :719 次序立法）。"""
    a = _bare_adapter()
    a._relogin()
    calls = [c[0] for c in a._api.method_calls]
    assert "Logout" in calls
    a._api.SubscribePublicTopic.assert_called_once_with(fake_binding.EMT_TE_RESUME_TYPE.RESTART)
    a._api.Login.assert_called_once_with("h", 1, "a", "p", 1)


# --- 注册表（test_td_registry 补面） ---

def test_emt_builder_registered_with_contract():
    from src.strategy_runner.td_registry import TD_BUILDERS
    assert "emt_emq" in TD_BUILDERS and callable(TD_BUILDERS["emt_emq"])


# --- 代码审 A/B 同判补钉 ---

def test_real_binding_shape_regression():
    """真实 .so 形状回归钉（B 审要求）：绑定属性面断言——P0-1 教训
    （exec_id/report_index 漏绑被 hasattr 假绿掩盖）。无 .so 时 skip。"""
    import importlib
    import importlib.util as ilu
    import os as _os
    so_dir = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..",
                                            "src", "strategy_framework", "emd"))
    if ilu.find_spec("emt_trader_api") is None:
        if not any(p == so_dir for p in sys_path_list()):
            import sys
            sys.path.insert(0, so_dir)
    try:
        mod = importlib.import_module("emt_trader_api")
    except ImportError:
        pytest.skip("本地无 emt_trader_api.so（CI/未编译）")
    for attr in ("exec_id", "report_index", "order_emt_id", "quantity", "trade_time"):
        assert attr in dir(mod.EMTTradeReport), f"EMTTradeReport 漏绑 {attr}（P0-1 形状回归）"
    for attr in ("yesterday_position", "sellable_qty"):
        assert attr in dir(mod.EMTQueryStkPositionRsp), f"Position 漏绑 {attr}（P1-6）"


def sys_path_list():
    import sys
    return sys.path


def test_event_stream_to_engine(fake_binding):
    """事件推流钉（A-P2-2：_emit→ee.put EVENT_ORDER/TRADE——此前零断言）。"""
    ee = MagicMock()
    a = _bare_adapter(_event_engine=ee)
    a._cid2sn["t7:e1:c1"] = 42
    a._on_order_raw(_FakeRaw(order_client_id=42), None, 555)
    assert ee.put.called
    ev = ee.put.call_args.args[0]
    from vnpy.trader.event import EVENT_ORDER
    assert ev.type == EVENT_ORDER and ev.data.vt_orderid == "EMT.1001"


def test_missing_tradeid_field_raises_visible(fake_binding):
    """反向钉：物化面缺 exec_id/report_index 时 tradeid 走 '0' 兜底而非静默炸
    （P0-1 修复后语义——绑定漏绑场景可观测降级）。"""
    a = _bare_adapter()
    raw = _FakeRaw()
    raw.exec_id = None
    raw.report_index = None
    a._on_trade_raw(raw, 555)
    td = list(a._trades.values())[0]
    assert td.tradeid == "0"   # 可观测兜底（非 AttributeError 被宏吞）
