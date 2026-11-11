"""D32 端口层（batch 172a）——Protocol 只定签名，不含实现。

依赖规则：ports → quant_common（层 0）；ports 不 import providers/第三方。
"""

from .account import AccountPort
from .execution import ExecutionPort
from .history import HistoryPort
from .market_data import MarketDataPort
from .research import ResearchPort

__all__ = [
    "ExecutionPort", "MarketDataPort", "HistoryPort", "ResearchPort", "AccountPort",
]
