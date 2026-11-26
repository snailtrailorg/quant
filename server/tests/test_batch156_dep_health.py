# -*- coding: utf-8 -*-
"""批 156 · 七钉（方案 v2 §4）+ 契约 v2。"""
from unittest.mock import MagicMock, patch


def test_contract_schema2_fields(monkeypatch):
    """T1：契约 schema 2——os.installed 明细（批 161：all_repos 采集面随批 161 os 随行落表重构，断言更新）。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks._dep_audit_impl)
    assert '"schema_version": 2' in src
    assert '"installed"' in src
    assert "'system'" in src   # 批 161：OS 判定随行落表（source=system 行）


def test_os_full_rows_schema_branch(monkeypatch):
    """T2：schema 2 产 OS 全量行（upgrades→actionable；repos→current；无坐标→unknown）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc._collect_components)
    assert '_sv == 2' in src                 # schema 1 走 155 旧路径（中间态钉）
    assert '_seen_best' in src               # multilib 去重
    assert '仓库无坐标' in src


def test_alias_purge_and_global_gc(monkeypatch):
    """T2（批184a 改写）：别名清理由迁移 0189 一次性完成；运行期只做**全域** GC——
    原实现只清 system/pip，rpm·binary 从不清理（永久累积）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc.run_security_scan)
    assert '_LEGACY8' not in src, "运行期别名清理应已迁移到 0189"
    # 批191（用户裁定）：三域统一「本轮未见即删」，不再留 3 天宽限
    for dom in ("pip", "binary", "os"):
        assert f"source='{dom}' AND last_seen < now()" in src, dom
    assert "timedelta(days=3)" not in src


def test_managed_by_derived_covers_sbom_only():
    """T3（批184a 改写）：SBOM 独有行不再注入「none 判定」——managed_by 由域派生，
    binary→none 已覆盖同一语义（判据从「有没有判定行」改为「域是什么」）。"""
    from src.health_monitor import security_collector as sc
    assert sc._MANAGED_BY["binary"] == "none"
    assert sc._MANAGED_BY["os"] == "repo"


def test_alert_cold_start(monkeypatch):
    """T4：alerted 标志+冷启动/跨阈值/回落（A1）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc.run_security_scan)
    assert 'alerted' in src and 'prev_actionable_n' in src
    assert 'dep_alert_threshold' in src      # threshold 可配
    assert '回落' in src                     # 恢复通知


def test_aggregation_endpoint(monkeypatch):
    """T5：dep-health 聚合端点存在且认证挂载。"""
    from src.web_api.routes import security as S
    assert hasattr(S, 'dep_health_groups')


def test_upsert_idempotent_two_runs():
    """钉1：两轮 UPSERT 行数不翻倍（ON CONFLICT 语义——inspect 钉）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc._upsert_components)
    assert 'SELECT version FROM security_component' in src   # 存在则 UPDATE/否则 INSERT


def test_component_ledger_dedupes_pip_name_variants():
    """importlib_metadata / importlib-metadata 历史双行 → 只留 checked_at 最新一行。"""
    from src.data_platform.security_store import dedupe_components
    rows = [
        {"source": "pip", "name": "importlib-metadata", "version": "8.7.1",
         "checked_at": "2026-11-25T05:40:54", "judge_class": "actionable", "judge_why": "x"},
        {"source": "pip", "name": "importlib_metadata", "version": "10.1.0",
         "checked_at": "2026-11-25T08:04:01", "judge_class": None, "judge_why": None},
    ]
    out = dedupe_components(rows)
    assert len(out) == 1 and out[0]["version"] == "10.1.0"
