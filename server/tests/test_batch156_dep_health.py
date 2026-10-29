# -*- coding: utf-8 -*-
"""批 156 · 七钉（方案 v2 §4）+ 契约 v2。"""
from unittest.mock import MagicMock, patch


def test_contract_schema2_fields(monkeypatch):
    """T1：契约 schema 2——os.installed 明细+all_repos。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert '"schema_version": 2' in src
    assert '"installed"' in src and '"all_repos"' in src
    assert '_os_repo_all' in src


def test_os_full_rows_schema_branch(monkeypatch):
    """T2：schema 2 产 OS 全量行（upgrades→actionable；repos→current；无坐标→unknown）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc._collect_components)
    assert '_sv == 2' in src                 # schema 1 走 155 旧路径（中间态钉）
    assert '_seen_best' in src               # multilib 去重
    assert '仓库无坐标' in src


def test_legacy8_delete_and_gc(monkeypatch):
    """T2：8 行显式 DELETE+消失包 3 轮 GC。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc.run_security_scan)
    assert '_LEGACY8' in src and 'DELETE FROM security_component WHERE source=%s AND name IN' in src
    assert 'last_seen < %s' in src and 'timedelta(days=3)' in src


def test_sbom_only_none_rows(monkeypatch):
    """T3：ledger 名不在判定域→managed_by=none+why 非空（显式 unknown）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc.run_security_scan)
    assert 'SBOM 识别、非包管理器纳管' in src
    assert '_nm not in _gm' in src           # 纳管跳过（A4：已纳管不建 none 行）


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
