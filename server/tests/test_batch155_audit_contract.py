# -*- coding: utf-8 -*-
# 批 155 契约钉——2026-11-01 批 161 语义迁移注记。
# 原四测钉旧语义（soft 1500/1-7 轮转/契约 pip 段）已被批 161 显式变更（test_batch161_audit.py 承接）。
# 保留未变行为的两钉：
from unittest.mock import MagicMock, patch


def test_three_ops_are_independent(monkeypatch):
    """2026-11-25 三任务拆分：扫描/依赖审计/漏洞库各自端点与任务，互不隐式派发。"""
    import inspect
    from pathlib import Path
    from src.web_api.routes import security as S
    from src.scheduler import tasks
    scan_code = chr(10).join(ln for ln in inspect.getsource(S.security_scan_trigger).splitlines()
                             if not ln.strip().startswith("#"))
    assert "dep_audit.delay()" not in scan_code          # 扫描不再顺带派 audit
    assert hasattr(S, "dep_audit_trigger") and hasattr(S, "grype_db_update_trigger")
    assert hasattr(tasks, "grype_db_update")
    app_src = Path("src/scheduler/app.py").read_text()
    assert '"dep-audit"' in app_src and "crontab(hour=4, minute=0)" in app_src
    assert '"grype-db-update"' in app_src and "crontab(hour=5, minute=0)" in app_src


def test_local_pkg_zero_upstream(monkeypatch):
    # local 包零上游请求（155 立法，批 161 形态更新——详见 161 套同名测试）。
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks._dep_audit_impl)
    assert 'if local:' in src
