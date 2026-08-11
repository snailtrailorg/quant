"""跨层联动告警单测：notify_on_failure（mock MessageChannel，失败不影响主流程）。"""
from unittest.mock import patch, MagicMock


def test_notify_on_failure_sends():
    from src.task_manager import notify_on_failure
    mock_ch = MagicMock()
    mock_ch.send.return_value = True
    with patch("src.alert_notify.channel.get_channel", return_value=mock_ch):
        notify_on_failure("测试失败", "测试原因", provider="wechat_work")
    mock_ch.send.assert_called_once_with("测试失败", "测试原因", level="error")


def test_notify_on_failure_no_channel():
    """无配置 channel 不报错"""
    from src.task_manager import notify_on_failure
    with patch("src.alert_notify.channel.get_channel", return_value=None):
        notify_on_failure("t", "b")  # 不抛


def test_notify_on_failure_send_exception():
    """send 异常不抛（告警失败不影响主流程）"""
    from src.task_manager import notify_on_failure
    mock_ch = MagicMock()
    mock_ch.send.side_effect = Exception("network")
    with patch("src.alert_notify.channel.get_channel", return_value=mock_ch):
        notify_on_failure("t", "b")  # 不抛


def test_notify_on_failure_get_channel_exception():
    """get_channel 异常不抛"""
    from src.task_manager import notify_on_failure
    with patch("src.alert_notify.channel.get_channel", side_effect=Exception("db error")):
        notify_on_failure("t", "b")  # 不抛
