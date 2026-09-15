"""批25：全局日志两套体系测试。

覆盖：log_sink 批量/幂等 flush/event 降级、cleanup_logs 批删循环、
GET /api/log 切 system_log+权限收紧+task_id 分支保留 task_logs、/api/audit/delete 退役 404。
"""
from unittest.mock import patch, MagicMock

import pytest


def _client():
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    return TestClient(app)


ADMIN = {"sub": "1", "username": "admin", "role": "admin", "db_role": "admin"}
VIEWER = {"sub": "9", "username": "v", "role": "viewer", "db_role": "viewer"}


def _conn(rows=None, rowcount=0):
    conn = MagicMock(); conn.__enter__.return_value = conn
    cur = MagicMock(); cur.fetchall.return_value = rows or []; cur.rowcount = rowcount
    conn.execute.return_value = cur
    return conn


class TestLogSink:
    def test_emit_batch_and_flush_idempotent(self):
        from src.data_platform import log_sink as LS
        sink = LS._Sink("test")
        sink.setFormatter(LS.logging.Formatter("%(message)s"))
        rec = LS.logging.LogRecord("m", LS.logging.INFO, "f", 1, "hello %s", ("world",), None)
        sink.emit(rec)
        assert len(sink._q) == 1
        with patch("src.data_platform.db.get_conn", return_value=_conn()):
            sink.flush()
            sink.flush()   # 幂等：二次空转
        assert len(sink._q) == 0

    def test_db_failure_drops_batch_not_raise(self, capsys):
        from src.data_platform import log_sink as LS
        sink = LS._Sink("test")
        sink._q.append(("INFO", "x", "m"))
        bad = MagicMock()
        bad.__enter__.side_effect = RuntimeError("db down")
        with patch("src.data_platform.db.get_conn", return_value=bad):
            sink._write([("INFO", "x", "m")])   # 不抛
        assert "dropped" in capsys.readouterr().err

    def test_event_without_sink_fallback_direct(self):
        from src.data_platform import log_sink as LS
        LS._sink = None
        c = _conn()
        with patch("src.data_platform.db.get_conn", return_value=c):
            LS.event("WARN", "email", "入队待发 → a@b.c ｜ t")
        sql = c.execute.call_args[0][0]
        assert "INSERT INTO system_log" in sql


class TestGetLogs:
    def test_no_param_reads_system_log(self):
        c = _conn(rows=[("INFO", "email", "已发送 → a@b.c", "2026-09-15 10:00:00")])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=c):
            r = _client().get("/api/log", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        sql = c.execute.call_args[0][0]
        assert "FROM system_log" in sql and "task_logs" not in sql

    def test_task_id_branch_reads_task_logs(self):
        c = _conn(rows=[("INFO", "step msg", "sync", "2026-09-15 10:00:00")])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=c):
            r = _client().get("/api/log?task_id=live:1", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        sql = c.execute.call_args[0][0]
        assert "FROM task_logs" in sql and "task_id = %s" in sql

    def test_viewer_forbidden(self):
        """批25 权限收紧：read→user_mgmt（运行日志=管理员面，用户裁定）。"""
        with patch("src.web_api.auth.verify_jwt", return_value=VIEWER), \
             patch("src.data_platform.db.get_conn", return_value=_conn()):
            r = _client().get("/api/log", headers={"Authorization": "Bearer t"})
        assert r.status_code == 403


class TestAuditDeleteRetired:
    def test_endpoint_gone(self):
        """批25：审计删除退役（系统记录，UI 简化用户裁定）——404。"""
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.data_platform.db.get_conn", return_value=_conn()):
            r = _client().post("/api/audit/delete", json={"ids": [1]},
                               headers={"Authorization": "Bearer t"})
        assert r.status_code == 404


class TestCleanupLogs:
    def test_batch_loop_until_short(self):
        import src.scheduler.tasks as T
        calls = iter([5000, 5000, 12])   # 三轮后短于批=停
        rows = []
        with patch("src.data_platform.db.get_conn", side_effect=lambda: (
            rows.append(1), _conn(rowcount=next(calls)))[1]):
            r = T.cleanup_logs()
        assert r["deleted"] == 10012
