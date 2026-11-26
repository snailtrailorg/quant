"""批 191：requirements-dev.txt 退役 + staging/prod venv 瘦身（prune 一次性通道）。"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_dev_requirements_retired_and_migrated():
    """文件退役；生产真需要的三包已并入 requirements.txt。"""
    assert not (_REPO / "server/requirements-dev.txt").exists()
    req = (_REPO / "server/requirements.txt").read_text().lower()
    for p in ("pybind11", "filelock", "importlib-metadata"):
        assert p in req, p


def test_lock_covers_migrated_and_excludes_retired():
    lock = (_REPO / "server/requirements.lock").read_text().lower()
    for p in ("pybind11==", "filelock==", "importlib-metadata=="):
        assert p in lock, p
    for p in ("ruff==", "pip-tools==", "akshare=="):
        assert p not in lock, f"退役包不应在 lock: {p}"


def test_deploy_chain_only_mentions_retirement_in_comments():
    """deploy 两处输入清单不得再有 requirements-dev 数据行（退役说明注释除外）。"""
    for rel in ("deploy/inventory/group_vars/all.yml", "deploy/playbooks/release.yml"):
        for line in (_REPO / rel).read_text().splitlines():
            if "requirements-dev" in line:
                assert line.lstrip().startswith("#"), f"{rel}: {line}"


def test_gc_three_domains_immediate_with_domain_guard():
    """批191：三域本轮未见即删；域级采集守卫（降级时跳过该域）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc.run_security_scan)
    for dom in ("pip", "binary", "os"):
        assert f"source='{dom}' AND NOT (name = ANY(%s::text[]))" in src, dom
    assert "timedelta(days=3)" not in src            # 宽限已撤
    assert "if _os_all:" in src                       # OS 域采集守卫


def test_gc_compares_round_names_not_time():
    """时间判据在 last_seen(date)+同日多轮下必然出错（曾清空全表）——必须按本轮名单。"""
    import inspect
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc.run_security_scan)
    assert "NOT (name = ANY(%s::text[]))" in src
    assert "last_seen < %s" not in src
    assert "last_seen < now()" not in src


def test_wrapper_prune_is_hardcoded_whitelist():
    """prune=一次性、硬编码三包（不接受任意包名=零注入面）。"""
    w = (_REPO / "deploy/wrappers/quant-pip-wrapper").read_text()
    assert "prune)" in w
    assert 'uninstall -y ruff pip-tools akshare' in w
    assert 'prune" >&2; exit 2' in w or "[ $# -eq 1 ]" in w
