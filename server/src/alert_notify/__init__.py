"""告警/通知 —— 企业微信/Discord/Server酱，分级路由+配额聚合。

用法:
    from src.alert_notify import AlertNotify
    AlertNotify.get().notify("critical", "熔断", "总回撤超限")
    AlertNotify.get().report("盘后报告", "今日盈亏+...")
"""

from .notify import AlertNotify

__all__ = ["AlertNotify"]
