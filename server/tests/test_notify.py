"""任务失败通知单测（PT7）：notify_on_failure 走通知中心（warn/task，站内铃铛）。"""
from unittest.mock import patch


def test_notify_on_failure_sends():
    with patch("src.alert_notify.notify") as mock_notify:
        from src.task_manager import notify_on_failure
        notify_on_failure("测试失败", "测试原因")
    mock_notify.assert_called_once_with("warn", "task", "测试失败", "测试原因", code="task.failed")  # W3 打码


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


def test_runbook_consistency():
    """W3 一致性：全站直调打码 code 字面量 ⊆ RUNBOOK 键（打码必有映射）。

    盲区声明：wrapper 变量透传链（main._alert 剥 code / make_alert 注入链）测不到，
    靠人工对照——改码时同步查 runbook.py。
    """
    import ast, pathlib
    from src.alert_notify.runbook import RUNBOOK
    server_root = pathlib.Path(__file__).parent.parent / "src"
    stamped = set()
    for f in server_root.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call):
                fn = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
                if fn in ("_alert", "safe_notify", "notify", "_notify", "alert", "_sa4_alert_once"):
                    for kw in node.keywords:
                        if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                            stamped.add(kw.value.value)
    missing = stamped - set(RUNBOOK)
    assert not missing, f"打码无映射(漂移!): {sorted(missing)}"


def test_push_channel_appends_runbook_line():
    """W3：外推组装——code 有映射时 body 尾部追加处置行；无 code 不追加。"""
    from unittest.mock import patch, MagicMock
    import importlib
    N = importlib.import_module("src.alert_notify.notify")
    sent = []
    ch = MagicMock(); ch.send.side_effect = lambda t, b, l: sent.append((t, b, l))
    r = MagicMock(); r.exists.return_value = 0
    with patch.object(N, "_redis", return_value=r), \
         patch.object(N, "should_push_external", return_value=True), \
         patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db down")), \
         patch("src.alert_notify.channel.get_channel", return_value=ch), \
         patch.object(N, "_quota_exceeded", return_value=False):
        N.notify("critical", "system", "t", "b", code="l3.failed")
        N.notify("critical", "system", "t2", "b2", code=None)
    assert "▸ 处置[L3 拉起失败]" in sent[0][1]
    assert "▸" not in sent[1][1]


def test_push_channel_truncates_before_append():
    """W3 盲审 A/B-P1：先截原 body 再拼行——5000 字符 body 不超通道限。"""
    from unittest.mock import patch, MagicMock
    import importlib
    N = importlib.import_module("src.alert_notify.notify")
    sent = []
    ch = MagicMock(); ch.send.side_effect = lambda t, b, l: sent.append(b)
    r = MagicMock(); r.exists.return_value = 0
    with patch.object(N, "_redis", return_value=r), \
         patch.object(N, "should_push_external", return_value=True), \
         patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db")), \
         patch("src.alert_notify.channel.get_channel", return_value=ch), \
         patch.object(N, "_quota_exceeded", return_value=False):
        N.notify("critical", "system", "t", "x" * 5000, code="l3.failed")
    body = sent[0]
    assert len(body) < 2600                       # 1900 截断 + 处置行,远离 4096 字节
    assert body.endswith(("。", "）", ")")) or "处置" in body[-600:]   # 处置行在尾部未截
