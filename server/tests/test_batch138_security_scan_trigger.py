"""批 138：手动触发安全扫描端点（POST /api/security/scan）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


class TestScanTrigger:
    def test_submits_and_returns_task_id(self):
        """接线钉：端点必须把既有 beat 任务 security_scan 入队（不是另写采集）。"""
        from src.web_api.routes import security as S
        fake = SimpleNamespace(id="task-abc")
        with patch("src.scheduler.tasks.security_scan.delay", return_value=fake) as d:
            out = S.security_scan_trigger({"username": "admin"})
        assert d.called
        assert out == {"status": "submitted", "task_id": "task-abc"}

    def test_route_is_depends_wrapped(self):
        """路由认证守门：checker 必须经 Depends() 包裹（裸参数默认值=静默短路）。"""
        import inspect
        from src.web_api.routes.security import security_scan_trigger
        src = inspect.getsource(security_scan_trigger)
        assert "Depends(require_perm" in src

    def test_no_sql_in_route(self):
        """路由层 SQL 冻结：触发面不落任何 SQL（采集真源在 collector/store）。"""
        import inspect
        from src.web_api.routes.security import security_scan_trigger
        src = inspect.getsource(security_scan_trigger)
        for kw in ("SELECT ", "INSERT ", "get_conn"):
            assert kw not in src, kw