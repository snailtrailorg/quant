"""批 179b 守门：扫描读侧零联网 + latest 三态真实映射。

批 179 曾把逐包 PyPI 联网（_fill_missing_latest + force 重查）搬进用户手动
security_scan 的关键路径，串行 N×10s 直接把扫描卡死。本批恢复「上游刷新只在
dep_audit 慢路径」，并钉死两条：① 读侧绝不联网 ② latest 缺失显示「未知」而
非「无更新」——不把没查到谎报成已确认最新。
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import urllib.request
from pathlib import Path

from src.health_monitor import security_collector as sc
from src.health_monitor.os_pkg import pypi_versions_cached
from src.quant_common.depjudge import has_update_of, judge


# ① 读侧三态：latest 的真实语义
def test_unknown_is_unknown_not_no_update():
    """上游版本不可得 = 未知；有明确判定的才允许 no。"""
    assert has_update_of("unknown") == "unknown"
    assert has_update_of(None) == "unknown"
    assert has_update_of("actionable") == "yes"
    assert has_update_of("in_constraint") == "yes"
    assert has_update_of("current") == "no"
    assert has_update_of("pinned") == "no"
    assert has_update_of("drift") == "no"
    assert has_update_of("unreachable") == "no"


def test_latest_missing_maps_to_unknown():
    """latest 未取到 → judge=unknown → 组件行「未知」（soupsieve 旧形态回归钉）。"""
    jc, why = judge("2.10", None, None, None, True, "pypi")
    assert jc == "unknown" and "上游" in why
    assert has_update_of(jc) == "unknown"


def test_latest_above_current_is_actionable():
    jc, _ = judge("2.10", None, "4.1", None, True, "pypi")
    assert jc == "actionable" and has_update_of(jc) == "yes"


def test_latest_equal_current_is_no_update():
    jc, _ = judge("4.1", None, "4.1", None, True, "pypi")
    assert jc == "current" and has_update_of(jc) == "no"


# ② 扫描读侧零联网：符号级静态守门（按专有符号判，不引用被测属性）
def test_scan_collector_has_no_pypi_refresh():
    text = Path(sc.__file__).read_text(encoding="utf-8")
    for banned in ("pypi_versions_cached", "pypi_versions(", "_fill_missing_latest",
                   "dep_gap_filled", "force=force"):
        assert banned not in text, f"扫描读侧不应出现上游刷新: {banned}"


def test_cached_signature_has_no_force():
    """缓存层恒读缓存——不再有绕过缓存的 force 通道。"""
    assert "force" not in inspect.signature(pypi_versions_cached).parameters


# ③ 运行时守门：_collect_components 不触碰网络，且组件真源=实际安装集
def test_collect_components_zero_network(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("扫描读侧不得联网")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    monkeypatch.setattr(sc, "_run", lambda *a, **kw: "soupsieve==2.10\n")
    monkeypatch.setattr(sc, "read_dep_rows", lambda lc: (
        [("soupsieve", "2.10", None, "unknown", "上游不可达或未登记", None)], False))

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    contract = {"schema_version": 2, "ts": now, "constraints": {}, "dev": [],
                "ok": True, "errors": [], "last_complete": {"ts": now},
                "os": {"pkg_mgr": "dnf", "upgrades": {}, "all_repos": {},
                       "installed": [], "installed_n": 0}}

    class _R:
        def get(self, key):
            return json.dumps(contract).encode() if key == "sec:dep_audit" else None

    monkeypatch.setattr("src.quant_common.redis_client.business_redis", lambda: _R())

    part = sc._collect_components()
    assert "soupsieve" in part["pip"]                      # 真源=pip 安装集
    rows = (part.get("dep_health") or {}).get("rows") or []
    assert any(r["name"] == "soupsieve" and r["has_update"] == "unknown" for r in rows)
    assert part["status"] == "ok"
