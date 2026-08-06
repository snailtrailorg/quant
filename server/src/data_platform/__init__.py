"""数据中台 —— 统一数据入口。

用法:
    from src.data_platform import platform
    platform.ensure_daily("600000.SH", "20260701", "20260710")
    bars = platform.get_bar("600000.SHSE", "1D", date(2026,7,1), date(2026,7,10))
"""

from .platform import platform, DataPlatform
from .schema import Bar, to_vt_symbol, parse_vt_symbol, to_ts_code

__all__ = ["platform", "DataPlatform", "Bar", "to_vt_symbol", "parse_vt_symbol", "to_ts_code"]