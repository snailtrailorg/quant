"""批 R1（2026-10-11 健壮性测试修复）：sync 入口闸 + audit_log 防 NUL。

来源：六阶段黑盒测试两个发现——
- %00 路径参数经 audit_log 落 PG → psycopg.DataError → 500（Internal Server Error）；
- backfill_from=abc 静默成功（脏值落 start 列）。

修：① audit_log fail-soft + NUL→␀（审计是旁路，永不炸业务）；
② trigger/symbol/backfill/delete 四端点 sid 白名单正则 + 日期 YYYYMMDD 校验（4xx）。
"""
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient


def _client():
    from src.web_api.main import app
    return TestClient(app, raise_server_exceptions=False)


_AUTH = {"Authorization": "Bearer t"}


def _perm_ok():
    """绕认证（仓内惯例＝test_route_auth_gate.py 同款：patch verify_jwt +
    load_effective_permissions——路由在注册时已绑定 require_perm 闭包，patch 工厂无效）。"""
    from contextlib import ExitStack
    return ExitStack(), patch("src.web_api.auth.verify_jwt",
                              return_value={"sub": 1, "username": "admin", "role": "admin", "db_role": "admin"}), \
        patch("src.web_api.auth.load_effective_permissions",
              return_value=({"data_sync", "read"}, set()))


@contextmanager
def _authed():
    with patch("src.web_api.auth.verify_jwt",
               return_value={"sub": 1, "username": "admin", "role": "admin", "db_role": "admin"}), \
         patch("src.web_api.auth.load_effective_permissions",
               return_value=({"data_sync", "read"}, set())):
        yield


class TestSidGate:
    def test_bad_sid_4xx(self):
        with _authed():
            c = _client()
            for bad in ("nonexistent xyz", "x'; drop", "%00", "UPPER", "a" * 65):
                r = c.post(f"/api/sync/trigger/{bad}", headers=_AUTH)
                assert r.status_code == 400, (bad, r.status_code)
                assert r.json().get("code") == "SYNC_SID_INVALID"

    def test_good_sid_passes_gate(self):
        """合法 sid 通过闸（worker 侧异步——只断言不再 4xx 在闸层）。"""
        with _authed(), patch("src.scheduler.tasks.sync_via_celery") as mc:
            from src.scheduler import tasks  # noqa: F401  确认可 import
            mc.delay.return_value.id = "tid-1"
            c = _client()
            r = c.post("/api/sync/trigger/st_list_sync", headers=_AUTH)
            assert r.status_code == 200
            assert r.json()["task_id"] == "tid-1"


class TestBackfillGate:
    def test_bad_date_4xx(self):
        with _authed():
            c = _client()
            for bad in ("abc", "2026-13-45", "20261345", "2099-01-01x"):
                r = c.post(f"/api/sync/trigger/st_list_sync?backfill_from={bad}", headers=_AUTH)
                assert r.status_code == 400, (bad, r.status_code)
                assert r.json().get("code") == "SYNC_BACKFILL_INVALID"

    def test_valid_date_passes(self):
        with _authed(), patch("src.scheduler.tasks.sync_via_celery") as mc:
            mc.delay.return_value.id = "tid-2"
            c = _client()
            r = c.post("/api/sync/trigger/st_list_sync?backfill_from=20260101", headers=_AUTH)
            assert r.status_code == 200

    def test_backfill_symbol_dates_validated(self):
        with _authed():
            c = _client()
            r = c.post("/api/sync/symbol/st_list_sync/000001.SZ/backfill",
                       json={"start": "garbage"}, headers=_AUTH)
            assert r.status_code == 400


class TestAuditNul:
    def test_audit_log_nul_sanitized(self):
        """NUL→␀ 后可写（不炸 psycopg）；DB 故障 fail-soft 放行。"""
        from src.data_platform.audit import audit_log, _no_nul
        assert _no_nul("ab\x00cd") == "ab␀cd"
        with patch("src.data_platform.db.get_conn") as mg:
            mg.return_value.__enter__.return_value.execute.side_effect = RuntimeError("db down")
            audit_log("u", "a", "t")   # 不 raise = fail-soft 生效

    def test_audit_log_normal_write(self):
        from src.data_platform.audit import audit_log
        with patch("src.data_platform.db.get_conn") as mg:
            conn = mg.return_value.__enter__.return_value
            audit_log("u\x00", "act", "tgt")
            sql_args = conn.execute.call_args[0][1]
            assert "␀" in sql_args[0] and "\x00" not in sql_args[0]
