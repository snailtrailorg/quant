"""D4：A股交易费用计算（层0，回测/实盘资金对账共享）。

费率口径（对齐 backtest.py set_fees 默认值，backtest.py:70-75）：
- 佣金：金额 × commission_rate（默认万五）
- 过户费：金额 × 0.001%（0.00001），双边，仅 A股股票
- 印花税：金额 × 0.05%（0.0005），仅卖出，仅 A股股票
- 可转债/场内基金（ETF/LOF/封基/REITs）免印花税免过户费
- 加密（binance/okx perp）maker/taker + 资金费 → D6 接入，此处仅佣金占位
"""
from __future__ import annotations

COMMISSION_RATE = 0.0005     # 万五
STAMP_TAX = 0.0005           # 印花税 0.05%（2023-08 减半后）
TRANSFER_FEE = 0.00001       # 过户费 0.001%


def symbol_market(symbol: str) -> str | None:
    """symbol → 市场分项（astock/convertible/etf/binance_perp/okx_perp），纯 symbol 规则。

    与 risk_control.risk._market_of 同源（此处层0抽离供费率/对账复用，后续可收编上层）。
    兼容裸代码（trade_log.symbol 是 vnpy TradeData.symbol 裸代码 "600000"，不带后缀）：
    A股=6 位数字裸代码或 SHSE/SZSE/SSE 后缀；可转债 11/12 开头；场内基金 50/51/56/58/15/16/18 开头。
    """
    if ".BINANCE" in symbol:
        return "binance_perp"
    if ".OKX" in symbol:
        return "okx_perp"
    code = symbol.split(".")[0]
    if code.startswith(("11", "12")):   # 沪/深可转债
        return "convertible"
    if code.startswith(("50", "51", "56", "58")) or code.startswith(("15", "16", "18")):
        return "etf"   # 场内基金全体（ETF/LOF/封基/REITs）
    if any(symbol.endswith(s) for s in (".SHSE", ".SZSE", ".SSE")) or (len(code) == 6 and code.isdigit()):
        return "astock"
    return None


def calc_trade_fee(symbol: str, action: str, amount: float,
                   commission_rate: float = COMMISSION_RATE) -> float:
    """单笔成交理论费用（本地计算，供资金对账「本地推导余额」扣减；回测口径对齐）。

    amount=成交额（price×volume）。佣金双边；过户费双边仅 A股股票；印花税仅卖出且 A股股票。
    可转债/场内基金免印花免过户（仅佣金）；加密 maker/taker 挂账 D6（此处仅佣金占位）。
    """
    if amount <= 0:
        return 0.0
    market = symbol_market(symbol)
    commission = amount * commission_rate
    if market == "astock":
        commission += amount * TRANSFER_FEE
        if str(action).upper() == "SELL":
            commission += amount * STAMP_TAX
    return commission
