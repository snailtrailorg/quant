"""后台任务管理单测（真实本地 DB，lifecycle + log，隔离 tid 不影响其他任务）。"""
import uuid
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _tid():
    return f"test-{uuid.uuid4().hex[:8]}"


def test_task_lifecycle():
    """create -> get -> update_heartbeat -> complete -> force_delete"""
    from src.task_manager import create_task, get_task, update_heartbeat, complete_task, force_delete_task
    tid = _tid()
    create_task(tid, "测试任务", "sync", "manual", "test", {"k": "v"})
    t = get_task(tid)
    assert t is not None
    assert t["status"] == "running"
    assert t["name"] == "测试任务"
    assert t["params"] == {"k": "v"}
    update_heartbeat(tid, {"current": 5, "total": 10, "pct": 50, "step": "step1"})
    t = get_task(tid)
    assert t["progress"]["pct"] == 50
    complete_task(tid, status="completed")
    t = get_task(tid)
    assert t["status"] == "completed"
    force_delete_task(tid)
    assert get_task(tid) is None


def test_task_log():
    """log_task 写日志，get_task 返回 logs"""
    from src.task_manager import create_task, log_task, get_task, force_delete_task
    tid = _tid()
    create_task(tid, "测试", "sync", "manual", "test")
    log_task(tid, "INFO", "开始同步", step_name="init")
    log_task(tid, "ERROR", "拉取失败", step_name="pull", sql_or_api="pro.daily")
    t = get_task(tid)
    assert len(t["logs"]) == 2
    levels = [l["level"] for l in t["logs"]]
    assert "INFO" in levels and "ERROR" in levels
    force_delete_task(tid)
    assert get_task(tid) is None


def test_list_tasks_filter():
    """list_tasks 按 status 过滤"""
    from src.task_manager import create_task, list_tasks, complete_task, force_delete_task
    tid = _tid()
    create_task(tid, "过滤测试", "sync", "manual", "test")
    # running 时能查到
    running = list_tasks(status="running", limit=1000)
    assert any(t["id"] == tid for t in running)
    complete_task(tid, status="completed")
    completed = list_tasks(status="completed", limit=1000)
    assert any(t["id"] == tid for t in completed)
    force_delete_task(tid)
