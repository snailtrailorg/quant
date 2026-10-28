"""批 138：手动触发安全扫描端点（POST /api/security/scan）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


class TestScanTrigger:
    def test_submits_and_returns_task_id(self):
        """接线钉：端点必须把既有 beat 任务 security_scan 入队（不是另写采集）。
        批 155 §4.4c：端点读 Valkey 锁/缓存——mock 无锁无缓存（fresh 判定走派发分支）。"""
        from unittest.mock import MagicMock
        from src.web_api.routes import security as S
        fake = SimpleNamespace(id="task-abc")
        client = MagicMock()
        client.get.return_value = None            # 无锁 -> 不 409；无缓存 -> 派 audit
        with patch("src.scheduler.tasks.security_scan.delay", return_value=fake) as d, \
             patch("src.quant_common.redis_client.business_redis",
                   MagicMock(return_value=client)) as bf, \
             patch("src.scheduler.tasks.dep_audit.delay", MagicMock()) as ad:
            out = S.security_scan_trigger({"username": "admin"})
        assert d.called and bf.called and ad.called
        assert out == {"status": "submitted", "task_id": "task-abc"}

    def test_lock_occupied_returns_409(self):
        """批 155 §4.4c：锁占用 -> 409 SEC_SCAN_RUNNING（拒绝入队非排队）。"""
        from unittest.mock import MagicMock
        from src.web_api.routes import security as S
        from src.web_api.errors import ApiError
        import pytest as _pt
        client = MagicMock()
        client.get.return_value = b"running-task-123"
        with patch("src.quant_common.redis_client.business_redis",
                   MagicMock(return_value=client)):
            with _pt.raises(ApiError) as ei:
                S.security_scan_trigger({"username": "admin"})
        assert ei.value.status_code == 409

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