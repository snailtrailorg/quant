"""批 63 Phase B：EmqMdGateway + EMQ tick 映射测试钉（mock 绑定层，不依赖 .so）。"""
import enum
import threading
import types
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest


# ——— mock 绑定模块（patch _load_emd_binding 注入）———

class _FakeEMQExchange(enum.IntEnum):
    SH = 1
    SZ = 2
    BJGZ = 5


class _FakeQuoteApi:
    """记录 CreateQuoteApi/RegisterSpi/Login/订阅退订，供断言。"""
    created = None
    login_ret = 0
    sub_ret = 0
    instances = []

    def __init__(self):
        self.spi = None
        self.subs = []
        self.unsubs = []
        _FakeQuoteApi.instances.append(self)

    @staticmethod
    def CreateQuoteApi(log_path, file_level, console_level):
        _FakeQuoteApi.created = (log_path, file_level, console_level)
        return _FakeQuoteApi()

    def RegisterSpi(self, spi):
        self.spi = spi

    def Login(self, ip, port, user, pwd):
        self.last_login = (ip, port, user, pwd)
        return _FakeQuoteApi.login_ret

    def SubscribeMarketData(self, tickers, ex):
        self.subs.append((list(tickers), ex))
        return _FakeQuoteApi.sub_ret

    def UnSubscribeMarketData(self, tickers, ex):
        self.unsubs.append((list(tickers), ex))
        return 0


class _FakeQuoteSpi:
    pass


def _fake_mod(login_ret=0, sub_ret=0):
    mod = types.SimpleNamespace()
    mod.QuoteApi = _FakeQuoteApi
    mod.QuoteSpi = _FakeQuoteSpi
    mod.EMQ_LOG_LEVEL = types.SimpleNamespace(INFO="INFO", ERROR="ERROR")
    mod.EMQ_EXCHANGE_TYPE = _FakeEMQExchange
    _FakeQuoteApi.login_ret = login_ret
    _FakeQuoteApi.sub_ret = sub_ret
    _FakeQuoteApi.instances = []
    _FakeQuoteApi.created = None
    return mod


def _fake_md(**over):
    d = types.SimpleNamespace(
        exchange_id=1, ticker="600000",
        last_price=10.5, pre_close_price=10.0, open_price=10.1, high_price=10.8, low_price=9.9,
        close_price=10.5, upper_limit_price=11.0, lower_limit_price=9.0,
        data_time=20260922093000000, qty=123400, turnover=1234567.8,
        bid=[10.49, 10.48, 10.47, 10.46, 10.45, 10.44, 10.43, 10.42, 10.41, 10.40],
        ask=[10.51, 10.52, 10.53, 10.54, 10.55, 10.56, 10.57, 10.58, 10.59, 10.60],
        bid_qty=[100, 200, 300, 400, 500, 600, 700, 800, 900, 1000],
        ask_qty=[110, 210, 310, 410, 510, 610, 710, 810, 910, 1010],
    )
    for k, v in over.items():
        setattr(d, k, v)
    return d


def _make_gw():
    """构造 EmqMdGateway，make_alert 打桩（避免 real safe_notify 侧效应）。"""
    from src.strategy_framework.md_gateway import EmqMdGateway
    with patch("src.strategy_framework.runtime.alerts.make_alert", return_value=MagicMock()):
        return EmqMdGateway(counters=MagicMock())


# ——— 注册 / 辅助纯函数 ———

def test_emq_registered():
    from src.strategy_framework.md_gateway import list_md_gateway_providers
    assert "emt_emq" in list_md_gateway_providers()


def test_parse_host_port():
    from src.strategy_framework.md_gateway import _parse_host_port
    assert _parse_host_port("1.2.3.4:8093", 8093) == ("1.2.3.4", 8093)
    assert _parse_host_port("h", 9) == ("h", 9)
    assert _parse_host_port("", 9) == ("", 9)
    assert _parse_host_port("h:abc", 9) == ("h", 9)


# ——— tick 映射（data_time / 交易所 / 五档截取）———

def test_md_to_tick_mapping():
    from src.strategy_framework.md_gateway import _md_to_tick
    tick = _md_to_tick(_fake_md())
    assert tick.symbol == "600000"
    assert tick.exchange.value == "SSE"
    # data_time YYYYMMDDHHMMSSsss → 中国时刻 tz-aware（行为等值 XTP）
    assert tick.datetime == datetime(2026, 9, 22, 9, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert tick.datetime.utcoffset().total_seconds() == 8 * 3600
    assert tick.last_price == 10.5
    assert tick.volume == 123400
    assert tick.turnover == 1234567.8
    assert tick.limit_up == 11.0 and tick.limit_down == 9.0
    assert tick.open_price == 10.1 and tick.high_price == 10.8 and tick.low_price == 9.9
    assert tick.pre_close == 10.0
    # 十档取五：bid[0:5]
    assert tick.bid_price_1 == 10.49 and tick.bid_price_5 == 10.45
    assert tick.bid_price_5 != 10.40   # 第六档不取
    assert tick.ask_price_1 == 10.51 and tick.ask_price_5 == 10.55
    assert tick.bid_volume_5 == 500 and tick.ask_volume_5 == 510


def test_md_to_tick_szse_and_unknown_exchange():
    from src.strategy_framework.md_gateway import _md_to_tick
    assert _md_to_tick(_fake_md(exchange_id=2)).exchange.value == "SZSE"
    # 港股通(3)/未知(100) 不在 L1 A 股范围 → 丢弃
    assert _md_to_tick(_fake_md(exchange_id=3)) is None
    assert _md_to_tick(_fake_md(exchange_id=100)) is None


def test_md_to_tick_zero_data_time_dropped():
    """data_time=0（盘前快照零值）前置丢弃，不喂 strptime（B-4）。"""
    from src.strategy_framework.md_gateway import _md_to_tick
    assert _md_to_tick(_fake_md(data_time=0)) is None


# ——— connect / subscribe / unsubscribe ———

def test_connect_login_ok():
    mod = _fake_mod(login_ret=0)
    gw = _make_gw()
    with patch("src.strategy_framework.md_gateway._load_emd_binding", return_value=mod):
        gw.connect({"emq_account": "u", "emq_password": "p"}, {"emq_l1_host": "1.2.3.4:8093"})
    assert gw.connected is True
    api = _FakeQuoteApi.instances[-1]
    assert api.last_login == ("1.2.3.4", 8093, "u", "p")
    assert api.spi is not None   # RegisterSpi 已注册 trampoline
    assert _FakeQuoteApi.created is not None
    assert _FakeQuoteApi.created[0].endswith("emd_quote_logs")


def test_connect_login_fail_raises():
    """登录失败 fail-fast（对齐 hub「建连异常 → exit 78」，防失聪僵尸）。"""
    mod = _fake_mod(login_ret=-1)
    gw = _make_gw()
    with patch("src.strategy_framework.md_gateway._load_emd_binding", return_value=mod):
        with pytest.raises(RuntimeError):
            gw.connect({"emq_account": "u", "emq_password": "p"}, {"emq_l2_host": "h:9"})
    assert _FakeQuoteApi.instances[-1].last_login == ("h", 9, "u", "p")


def test_subscribe_and_unsubscribe():
    mod = _fake_mod(login_ret=0)
    gw = _make_gw()
    with patch("src.strategy_framework.md_gateway._load_emd_binding", return_value=mod):
        gw.connect({"emq_account": "u", "emq_password": "p"}, {"emq_l1_host": "h:9"})
    gw.subscribe("600000.SHSE")
    gw.subscribe("000001.SZSE")
    api = _FakeQuoteApi.instances[-1]
    assert api.subs == [(["600000"], _FakeEMQExchange.SH), (["000001"], _FakeEMQExchange.SZ)]
    gw.unsubscribe("600000.SHSE")
    assert api.unsubs == [(["600000"], _FakeEMQExchange.SH)]


def test_subscribe_skips_when_disconnected():
    mod = _fake_mod(login_ret=0)
    gw = _make_gw()
    with patch("src.strategy_framework.md_gateway._load_emd_binding", return_value=mod):
        gw.connect({"emq_account": "u", "emq_password": "p"}, {"emq_l1_host": "h:9"})
    gw._connected = False   # 模拟 OnError 断连
    gw.subscribe("600000.SHSE")
    assert _FakeQuoteApi.instances[-1].subs == []


def test_subscribe_failure_alerts():
    """订阅请求返回非 0（服务器拒收）→ 告警可见化（B-3）。"""
    mod = _fake_mod(login_ret=0, sub_ret=-1)
    gw = _make_gw()
    with patch("src.strategy_framework.md_gateway._load_emd_binding", return_value=mod):
        gw.connect({"emq_account": "u", "emq_password": "p"}, {"emq_l1_host": "h:9"})
    gw.subscribe("600000.SHSE")
    assert gw._alert.call_count == 1
    assert gw._alert.call_args.kwargs["code"] == "emq.sub-fail"


def test_on_sub_result_error_alerts():
    """OnSubMarketData 回调 error_id 非 0 → 告警可见化（B-3）。"""
    gw = _make_gw()
    gw._on_sub_result(types.SimpleNamespace(ticker="600000"),
                      types.SimpleNamespace(error_id=100, error_msg="rejected"))
    assert gw._alert.call_args.kwargs["code"] == "emq.sub-fail"


# ——— 回调面：tick 入队 → 消费线程 cb / OnError 断连 ———

def test_tick_flows_to_cb():
    gw = _make_gw()
    received = []
    done = threading.Event()
    gw.set_on_tick(lambda t: (received.append(t), done.set()))
    gw._on_depth(_fake_md())
    assert done.wait(timeout=2.0), "worker 未在超时内消费 tick"
    assert received[0].symbol == "600000"
    assert received[0].last_price == 10.5


def test_on_error_disconnects():
    gw = _make_gw()
    gw._connected = True
    gw._on_error(types.SimpleNamespace(error_id=1, error_msg="boom"))
    assert gw.connected is False
