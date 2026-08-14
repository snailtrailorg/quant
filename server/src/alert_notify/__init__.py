"""通知中心 —— 站内铃铛（PG notifications）+ 按规则外部推送。

用法:
    from src.alert_notify import notify
    notify("critical", "email", "邮件发送最终失败", "收件人...")   # 站内 + 按规则外推
    from src.alert_notify import report
    report("盘后报告", "今日盈亏+...")                             # 订阅型：站内 + 外推
"""

from .notify import notify, report, visible_categories, should_push_external, cleanup, CATEGORY_ROLES

__all__ = ["notify", "report", "visible_categories", "should_push_external", "cleanup", "CATEGORY_ROLES"]
