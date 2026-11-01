# -*- coding: utf-8 -*-
# 批 155 契约钉——2026-11-01 批 161 语义迁移注记。
# 原四测钉旧语义（soft 1500/1-7 轮转/契约 pip 段）已被批 161 显式变更（test_batch161_audit.py 承接）。
# 保留未变行为的两钉：
from unittest.mock import MagicMock, patch


def test_fresh_check_requires_schema(monkeypatch):
    # 批 155 §4.1（未变行为）：旧 schema 缓存不得判 fresh 压住 audit 派发。
    import inspect
    from src.web_api.routes import security as S
    src = inspect.getsource(S.security_scan_trigger)
    assert 'c.get("schema_version") == 2' in src


def test_local_pkg_zero_upstream(monkeypatch):
    # local 包零上游请求（155 立法，批 161 形态更新——详见 161 套同名测试）。
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert 'if local:' in src
