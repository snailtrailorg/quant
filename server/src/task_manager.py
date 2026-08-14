"""后台任务管理（PT1 平台化核心）：统一 tasks/task_logs 表 + CRUD + 卡死检测。

所有异步任务（回测/同步/AI/策略）纳入统一 tasks 表：
- create_task / update_heartbeat / complete_task / log_task
- detect_stuck：last_heartbeat 超时 + running -> stuck
- list_tasks / get_task / terminate / force_delete

复用 SyncLock 心跳理念（数据同步已有），推广到所有任务。
"""
from __future__ import annotations
import json
import logging
from src.data_platform.db import get_conn

logger = logging.getLogger("task_manager")


def create_task(task_id: str, name: str, task_type: str, trigger_type: str,
                trigger_user: str, params: dict | None = None) -> None:
    """创建任务（status='running'，last_heartbeat=now）。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, name, type, trigger_type, trigger_user, status, "
            "progress, params, last_heartbeat, start_time, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,'running',%s,%s,now(),now(),now(),now()) "
            "ON CONFLICT (id) DO UPDATE SET status='running', last_heartbeat=now(), updated_at=now()",
            (task_id, name, task_type, trigger_type, trigger_user,
             json.dumps({"current": 0, "total": 0, "pct": 0, "step": ""}),
             json.dumps(params or {})))
        conn.commit()


def update_heartbeat(task_id: str, progress: dict | None = None) -> None:
    """更新心跳（progress 可选：{current, total, pct, step}）。"""
    with get_conn() as conn:
        if progress:
            conn.execute(
                "UPDATE tasks SET last_heartbeat=now(), progress=%s, updated_at=now() WHERE id=%s",
                (json.dumps(progress), task_id))
        else:
            conn.execute(
                "UPDATE tasks SET last_heartbeat=now(), updated_at=now() WHERE id=%s", (task_id,))
        conn.commit()


def complete_task(task_id: str, status: str = "completed", error: str | None = None) -> None:
    """完成任务（completed/failed/terminated）。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status=%s, end_time=now(), error_message=%s, updated_at=now() WHERE id=%s",
            (status, error, task_id))
        conn.commit()


def log_task(task_id: str, level: str, message: str, step_name: str | None = None,
             sql_or_api: str | None = None) -> None:
    """写任务日志（故障定位：step/sql_or_api/resource）。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO task_logs (task_id, level, message, step_name, sql_or_api) "
            "VALUES (%s,%s,%s,%s,%s)",
            (task_id, level, message, step_name, sql_or_api))
        conn.commit()


def list_tasks(status: str | None = None, limit: int = 100) -> list[dict]:
    """列任务（可按 status 过滤）。"""
    with get_conn() as conn:
        if status:
            cur = conn.execute(
                "SELECT id, name, type, trigger_type, trigger_user, status, progress, "
                "last_heartbeat, error_message, start_time, end_time "
                "FROM tasks WHERE status=%s ORDER BY updated_at DESC LIMIT %s", (status, limit))
        else:
            cur = conn.execute(
                "SELECT id, name, type, trigger_type, trigger_user, status, progress, "
                "last_heartbeat, error_message, start_time, end_time "
                "FROM tasks ORDER BY updated_at DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "trigger_type": r[3], "trigger_user": r[4],
             "status": r[5], "progress": json.loads(r[6]) if r[6] else {},
             "last_heartbeat": str(r[7]) if r[7] else None, "error_message": r[8],
             "start_time": str(r[9]) if r[9] else None, "end_time": str(r[10]) if r[10] else None}
            for r in rows]


def get_task(task_id: str) -> dict | None:
    """任务详情 + 最近日志。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, name, type, trigger_type, trigger_user, status, progress, params, "
            "last_heartbeat, error_message, start_time, end_time FROM tasks WHERE id=%s", (task_id,))
        r = cur.fetchone()
        if not r:
            return None
        cur = conn.execute(
            "SELECT level, message, step_name, sql_or_api, created_at FROM task_logs "
            "WHERE task_id=%s ORDER BY created_at DESC LIMIT 50", (task_id,))
        logs = [{"level": l[0], "message": l[1], "step_name": l[2], "sql_or_api": l[3],
                 "created_at": str(l[4]) if l[4] else None} for l in cur.fetchall()]
    return {"id": r[0], "name": r[1], "type": r[2], "trigger_type": r[3], "trigger_user": r[4],
            "status": r[5], "progress": json.loads(r[6]) if r[6] else {}, "params": json.loads(r[7]) if r[7] else {},
            "last_heartbeat": str(r[8]) if r[8] else None, "error_message": r[9],
            "start_time": str(r[10]) if r[10] else None, "end_time": str(r[11]) if r[11] else None,
            "logs": logs}


def terminate_task(task_id: str) -> None:
    """终止任务（status='terminated'）。实际进程 kill 由调用方（有 pid 时）。"""
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET status='terminated', end_time=now(), updated_at=now() WHERE id=%s", (task_id,))
        conn.commit()


def force_delete_task(task_id: str) -> None:
    """强制删除（卡死时清理，删 tasks + task_logs）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM task_logs WHERE task_id=%s", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id=%s", (task_id,))
        conn.commit()


def detect_stuck(timeout_s: int = 300) -> int:
    """卡死检测：last_heartbeat 超时 + running -> stuck。返回标记数。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='stuck' WHERE status='running' "
            "AND last_heartbeat < now() - (%s || ' seconds')::interval RETURNING id", (str(timeout_s),))
        stuck_ids = [r[0] for r in cur.fetchall()]
        conn.commit()
    if stuck_ids:
        logger.warning(f"卡死检测标记 {len(stuck_ids)} 个任务: {stuck_ids}")
    return len(stuck_ids)

def notify_on_failure(title: str, body: str, provider: str = "wechat_work") -> None:
    """任务失败通知（PT7 跨层联动）→ 通知中心（站内铃铛；warn 级不外推，按 2026-08-14 推送规则）。"""
    try:
        from src.alert_notify import notify
        notify("warn", "task", title, body)
    except Exception as e:
        logger.warning(f"告警发送失败: {e}")
