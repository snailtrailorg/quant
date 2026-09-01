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


def test_notify_code_passthrough_to_insert():
    """web 长尾批：code 参数落到 notifications INSERT（结构化 body/runbook 键）。"""
    from unittest.mock import MagicMock
    import importlib
    N = importlib.import_module("src.alert_notify.notify")   # 包 __init__ 把 .notify 重绑成函数，importlib 拿真模块
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = [1]
    r = MagicMock()
    r.exists.return_value = 0
    with patch.object(N, "_redis", return_value=r), \
         patch("src.data_platform.db.get_conn", return_value=conn), \
         patch.object(N, "should_push_external", return_value=False):
        N.notify("critical", "system", "t", "b", code="l3.failed")
    sql = conn.execute.call_args[0][0]
    args = conn.execute.call_args[0][1]
    assert "code" in sql and "%s,%s,%s,%s,%s,%s" in sql
    assert args[-1] == "l3.failed"


def test_safe_notify_code_kwarg():
    """safe_notify code 透传（runner/_alert 消费面）。"""
    import importlib
    N = importlib.import_module("src.alert_notify.notify")
    with patch.object(N, "notify") as p:
        N.safe_notify("critical", "t", "b", code="frozen.intercept")
    p.assert_called_once_with("critical", "system", "t", "b", code="frozen.intercept")
