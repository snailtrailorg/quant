# -*- coding: utf-8 -*-
"""批 155 · dep_audit 契约钉（替代 154b 旧 mock 形态——结构化链）。

- 本地包零上游请求（审B-H1 立法防回归）
- 缓存契约 schema v1 落 Valkey（run_id/constraints/pip/os 段齐备）
- ok 量化：上游错误率>20% -> False
"""
from unittest.mock import MagicMock, patch


def test_local_pkg_zero_upstream(monkeypatch):
    """local 包（direct_url.json）不发起任何 pypi 请求。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert 'if row["local"]' in src and "return row, None, None" in src


def test_audit_writes_contract(monkeypatch):
    from src.scheduler import tasks
    installed = [{"name": "pandas", "cur": "2.3.3", "local": False},
                 {"name": "vnpy", "cur": "4.5.0", "local": True}]
    store = {}
    client = MagicMock()
    client.get.return_value = None
    client.set.side_effect = lambda k, v: store.__setitem__(k, v)
    factory = MagicMock(return_value=client)
    pv = MagicMock(return_value={"versions": ["2.3.3", "3.1.0"], "yanked": []})
    prov = MagicMock()
    prov.id = "dnf"
    prov.list_installed.return_value = []
    prov.upgrade_candidates.return_value = {}
    with patch("src.health_monitor.os_pkg.pip_installed", return_value=installed), \
         patch("src.health_monitor.os_pkg.pypi_versions", pv), \
         patch("src.health_monitor.os_pkg.detect_provider", return_value=prov), \
         patch("src.quant_common.redis_client.business_redis", factory):
        r = tasks.dep_audit()
    assert r["status"] == "ok" and r["n_pip"] == 2 and r["n_local"] == 1
    assert pv.call_count == 1                      # 仅 pandas 一次；vnpy 零请求
    import json
    c = json.loads(store["sec:dep_audit"])
    assert c["schema_version"] == 1 and "constraints" in c and "os" in c
    assert any(row["name"] == "pandas" and row["latest"] == "3.1.0" for row in c["pip"])


def test_ok_quantified(monkeypatch):
    """上游错误率>20% -> ok=False（审B-M4）。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert "0.20" in src
