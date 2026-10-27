# -*- coding: utf-8 -*-
"""批 154 补② · dep_audit 异步预算任务三钉。"""
from unittest.mock import MagicMock, patch


def test_task_registered():
    from src.scheduler.app import app
    ks = app.conf.beat_schedule
    assert "dep-audit" in ks and ks["dep-audit"]["schedule"]


def test_collector_reads_cache_not_subprocess():
    """扫描侧只读缓存——内联 pip 子进程已删（207s 阻塞根除）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc._collect_components)
    assert 'sec:dep_outdated' in src
    assert 'pip", "list", "--outdated"' not in src


def test_dep_audit_writes_cache(monkeypatch):
    """dep_audit：pip 出 JSON→写 Valkey（ISO 时间戳+items）。"""
    from src.scheduler import tasks
    fake = MagicMock(stdout='[{"name":"PyYAML","version":"6.0","latest_version":"6.1"}]', returncode=0)
    store = {}
    br = MagicMock()
    br.set.side_effect = lambda k, v: store.__setitem__(k, v)
    with patch("subprocess.run", return_value=fake), \
         patch("src.quant_common.redis_client.business_redis", br):
        r = tasks.dep_audit()
    assert r["status"] == "ok" and r["n_outdated"] == 1
    assert "sec:dep_outdated" in store and "pyyaml" in store["sec:dep_outdated"]
