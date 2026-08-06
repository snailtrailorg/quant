"""数据同步模块 -- 通用增量/全量同步引擎。

用法:
    from src.data_sync import sync
    result = sync("trade_cal")  # 同步交易日历
    result = sync("cb_daily")   # 同步可转债日线

per-symbol（完整性驱动）:
    from src.data_sync import sync_symbol, backfill_symbol, delete_symbol, sync_all
    sync_symbol("astock_daily", "000001.SZ")  # 空->全量/有数据->增量
"""

from .engine import (
    sync, _HANDLERS,
    sync_symbol, backfill_symbol, delete_symbol, sync_all,
)

__all__ = ["sync", "sync_symbol", "backfill_symbol", "delete_symbol", "sync_all"]
