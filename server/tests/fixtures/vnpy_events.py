"""批 172b · vnpy 事件对象构造器（字段与 vnpy 4.5 1:1）。

测试专用：让 translate/等价矩阵用真实 vnpy 对象，不手搓字段名。
"""
from __future__ import annotations

from datetime import datetime

from vnpy.trader.constant import Direction, Exchange, Offset, Status
from vnpy.trader.object import AccountData, OrderData, PositionData, TradeData


def order_data(
    status: Status = Status.NOTTRADED,
    *,
    orderid: str = "1",
    vt: str = "TEST.1",
    symbol: str = "600000.XSHG",
    direction: Direction = Direction.LONG,
    volume: float = 100,
    traded: float = 0,
    price: float = 10.0,
    dt: datetime | None = None,
    gateway: str = "TEST",
) -> OrderData:
    d = OrderData(
        symbol=symbol, exchange=Exchange.SSE, orderid=orderid,
        direction=direction, offset=Offset.NONE, price=price, volume=volume,
        traded=traded, status=status, datetime=dt, gateway_name=gateway,
    )
    d.vt_orderid = vt
    return d


def trade_data(
    *,
    tradeid: str = "T1",
    vt_trade: str = "TEST.T1",
    orderid: str = "1",
    vt_order: str = "TEST.1",
    symbol: str = "600000.XSHG",
    direction: Direction = Direction.LONG,
    volume: float = 100,
    price: float = 10.0,
    dt: datetime | None = None,
    gateway: str = "TEST",
) -> TradeData:
    d = TradeData(
        symbol=symbol, exchange=Exchange.SSE, orderid=orderid, tradeid=tradeid,
        direction=direction, offset=Offset.NONE, price=price, volume=volume,
        datetime=dt, gateway_name=gateway,
    )
    d.vt_orderid = vt_order
    d.vt_tradeid = vt_trade
    return d


def position_data(
    *,
    symbol: str = "600000.XSHG",
    volume: float = 1000,
    frozen: float = 200,
    price: float = 9.05,
    pnl: float = 50.0,
    direction: Direction = Direction.LONG,
    yd_volume: float = 800,
    gateway: str = "TEST",
) -> PositionData:
    return PositionData(
        symbol=symbol, exchange=Exchange.SSE, direction=direction,
        volume=volume, frozen=frozen, price=price, pnl=pnl,
        yd_volume=yd_volume, gateway_name=gateway,
    )


def account_data(
    *, accountid: str = "ACC1", balance: float = 1_000_000.0,
    frozen: float = 10_000.0, gateway: str = "TEST",
) -> AccountData:
    return AccountData(accountid=accountid, balance=balance, frozen=frozen, gateway_name=gateway)
