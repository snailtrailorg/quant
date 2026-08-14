"""任务失败通知单测（PT7）：notify_on_failure 走通知中心（warn/task，站内铃铛）。"""
from unittest.mock import patch


def test_notify_on_failure_sends():
    with patch("src.alert_notify.notify") as mock_notify:
        from src.task_manager import notify_on_failure
        notify_on_failure("测试失败", "测试原因")
    mock_notify.assert_called_once_with("warn", "task", "测试失败", "测试原因")


def test_notify_on_failure_exception_not_raise():
    """通知中心异常不影响主流程。"""
    with patch("src.alert_notify.notify", side_effect=Exception("db down")):
        from src.task_manager import notify_on_failure
        notify_on_failure("t", "b")  # 不抛
