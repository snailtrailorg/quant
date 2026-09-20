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
        # 批32：SELECT 加 id 首列（游标键成分）→ 行 5 元组
        from datetime import datetime as _dt
        c = _conn(rows=[(1, "INFO", "email", "已发送 → a@b.c", _dt.fromisoformat("2026-09-15T10:00:00+08:00"))])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=c):
            r = _client().get("/api/log", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        sql = c.execute.call_args[0][0]
        assert "FROM system_log" in sql and "task_logs" not in sql

    def test_task_id_branch_reads_task_logs(self):
        from datetime import datetime as _dt
        c = _conn(rows=[("INFO", "step msg", "sync", _dt.fromisoformat("2026-09-15T10:00:00+08:00"))])
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


def _cfg_conn():
    """按 SQL 前缀分派的精准 mock（盲审 B P1-2：万能 conn 会被 require_perm/audit_log
    与端点交错污染——权限行/value_type 行必须分开喂）。"""
    conn = MagicMock(); conn.__enter__.return_value = conn

    def _exec(sql, *a, **kw):
        cur = MagicMock()
        if sql.startswith("SELECT value_type"):
            cur.fetchone.return_value = ("int",)
        elif sql.startswith("SELECT subject_id") or sql.startswith("SELECT resource, effect"):
            cur.fetchall.return_value = [("admin", "system_config", "allow")]   # 三列 SQL 喂三元组（A P2-2：两元组解包炸=靠异常回退放行）
        return cur
    conn.execute = MagicMock(side_effect=_exec)
    return conn


class TestCleanupLogs:
    def test_batch_loop_until_short(self):
        """批删循环（批28-7 适配：配置 mock 为 30/0——log 清理/audit 永久，get_conn 只被删循环消耗）。"""
        import src.scheduler.tasks as T
        calls = iter([5000, 5000, 12])   # 三轮后短于批=停
        with patch.object(T, "_read_int_cfg", side_effect=[30, 0]), \
             patch("src.data_platform.db.get_conn", side_effect=lambda: _conn(rowcount=next(calls))):
            r = T.cleanup_logs()
        assert r["deleted"] == 10012
        assert r["audit_deleted"] == 0   # audit=0 永久缺省不清

    def test_log_zero_skips_all(self):
        """批28-7 盲审 P0 负例：log_retention_days=0（=不清理）——零删除零连接，杜绝全表清空。"""
        import src.scheduler.tasks as T
        with patch.object(T, "_read_int_cfg", side_effect=[0, 0]), \
             patch("src.data_platform.db.get_conn", side_effect=AssertionError("不应触库")):
            r = T.cleanup_logs()
        assert r == {"deleted": 0, "audit_deleted": 0}

    def test_negative_days_skips(self):
        """负值同样不清理（任务侧纵深——API 校验是第一道，直改库是第三道场景）。"""
        import src.scheduler.tasks as T
        with patch.object(T, "_read_int_cfg", side_effect=[-5, 0]), \
             patch("src.data_platform.db.get_conn", side_effect=AssertionError("不应触库")):
            r = T.cleanup_logs()
        assert r == {"deleted": 0, "audit_deleted": 0}

    def test_audit_positive_cleans_both(self):
        """audit_retention_days>0：两表都清（audit 并入本任务，返回双计数）。"""
        import src.scheduler.tasks as T
        calls = iter([5000, 12, 5000, 7])   # system_log 两轮 + audit 两轮
        with patch.object(T, "_read_int_cfg", side_effect=[30, 7]), \
             patch("src.data_platform.db.get_conn", side_effect=lambda: _conn(rowcount=next(calls))):
            r = T.cleanup_logs()
        assert r == {"deleted": 5012, "audit_deleted": 5007}

    def test_read_int_cfg_fallback(self):
        """配置读不到/解析失败回落缺省（独立短连接 try/except 范式）。"""
        import src.scheduler.tasks as T
        with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db down")):
            assert T._read_int_cfg("log_retention_days", 30) == 30
        dirty = _conn(); dirty.execute.return_value.fetchone.return_value = ("abc",)   # 脏值行：int('abc') 抛 → 回落
        with patch("src.data_platform.db.get_conn", return_value=dirty):
            assert T._read_int_cfg("audit_retention_days", 0) == 0

    def test_retention_negative_rejected_by_api(self):
        """批28-7 盲审 P0：保留两键负值 400（interval 负天数=删全表，audit 不可逆损失级）。"""
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.routes.system.get_conn", return_value=_cfg_conn()):   # 名字绑定式 import——patch 须打模块名
            r = _client().post("/api/system-config/log_retention_days", json={"value": -5},
                               headers={"Authorization": "Bearer t"})
        assert r.status_code == 400

    def test_retention_zero_accepted(self):
        """0=不清理：合法值放行（与 audit 永久缺省语义统一）。"""
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.routes.system.get_conn", return_value=_cfg_conn()), \
             patch("src.data_platform.db.get_conn", return_value=_cfg_conn()):   # A P1-1：audit_log 链（audit.py 函数内 import 打源名）——不 patch 则真连库+污染审计表
            r = _client().post("/api/system-config/audit_retention_days", json={"value": 0},
                               headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
