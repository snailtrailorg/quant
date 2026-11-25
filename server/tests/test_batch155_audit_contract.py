# -*- coding: utf-8 -*-
# 批 155 契约钉——2026-11-01 批 161 语义迁移注记。
# 原四测钉旧语义（soft 1500/1-7 轮转/契约 pip 段）已被批 161 显式变更（test_batch161_audit.py 承接）。
# 保留未变行为的两钉：
from unittest.mock import MagicMock, patch


def test_manual_scan_always_dispatches_dep_audit(monkeypatch):
    """2026-11-25 演进：手动刷新=要「现在就准」，判定列必须同轮重算，不再受 30h 档。

    旧行为（fresh 即跳过）导致升级后页面仍按旧判定报可升级（实测 54 条假阳性）。
    """
    import inspect
    from src.web_api.routes import security as S
    src = inspect.getsource(S.security_scan_trigger)
    code = chr(10).join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "dep_audit.delay()" in code
    assert "DEP_AUDIT_FRESH_H" not in code   # 注释可提旧机制，代码不得再用


def test_local_pkg_zero_upstream(monkeypatch):
    # local 包零上游请求（155 立法，批 161 形态更新——详见 161 套同名测试）。
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert 'if local:' in src
