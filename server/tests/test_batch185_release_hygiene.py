"""批185 守门：apt 只留接口 / 扫描器注册 / 契约 all_repos / finding 列改名。

对应 184 审核登记的 5 项挂账（apt 一项按用户裁定只保留接口）。
"""
import inspect

import pytest


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM security_finding LIMIT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def test_apt_keeps_interface_only():
    """apt 只保留接口（用户裁定 2026-11-16）：方法在、返回诚实空集。"""
    from src.health_monitor.os_pkg import DpkgApt
    p = DpkgApt()
    assert p.id == "apt"
    assert p.upgrade_candidates() == {}
    assert p.repo_packages() == set()


def test_dnf_provider_exposes_repo_packages():
    from src.health_monitor.os_pkg import RpmDnf
    assert hasattr(RpmDnf, "repo_packages")


def test_scanner_reports_missing_binaries(monkeypatch):
    """工具探测原语（os_pkg.detect_scanner）——**不是注册表**，只做 which 单入口。"""
    import src.health_monitor.os_pkg as o
    monkeypatch.setattr(o._shutil, "which", lambda b: None)
    st = o.detect_scanner().status()
    assert st["available"] is False
    assert "grype" in st["reason"] and "syft" in st["reason"]


def test_scanner_available_when_present(monkeypatch):
    import src.health_monitor.os_pkg as o
    monkeypatch.setattr(o._shutil, "which", lambda b: "/usr/local/bin/" + b)
    st = o.detect_scanner().status()
    assert st["available"] is True and st["reason"] == ""


def test_scanner_not_in_health_rule_chain():
    """扫描器缺失**不进** health_monitor 的 R 系列（那条链响应是重启服务；
    工具缺失响应是装二进制/查 DB/重扫 ⇒ 归安全域 security.stale）。"""
    import inspect
    from src.health_monitor import monitor, collector
    assert "scanner_unavailable" not in inspect.getsource(monitor)
    assert 'snap["scanner"]' not in inspect.getsource(collector.collect)


def test_stale_decision_paths():
    """停摆判定六条路径（纯函数）：首发/提醒/恢复/抖动/闸门/脏值。"""
    from src.health_monitor.security_collector import stale_decision as d
    assert d(0, True, False) == (1, "none")        # 第 1 轮不打扰
    assert d(1, True, False) == (2, "alert")       # 第 2 轮告警
    assert d(2, True, True) == (3, "none")         # 24h 内已提醒过
    assert d(2, True, False) == (3, "remind")      # 过了闸再提醒（防永久静默）
    assert d(5, False, True) == (0, "recovered")   # 恢复通知
    assert d(1, False, False) == (0, "none")       # 未达 2 轮不发恢复（单轮抖动）


class _FakeBr:
    def __init__(self, v):
        self.v = v
        self.sets = []

    def get(self, k):
        return self.v

    def set(self, k, v, ex=None):
        self.v = v
        self.sets.append((k, v))


def test_streak_read_resets_dirty_value():
    """脏值必须**重写键**——否则每轮抛错被吞，streak 永不推进、告警永久静默。"""
    from src.health_monitor.security_collector import _streak_read
    br = _FakeBr(b"not-a-number")
    assert _streak_read(br, "k") == 0
    assert br.v == 0 and br.sets, "脏值未回写（下一轮仍会抛）"


def test_local_db_update_time_recorded():
    """漏洞库卡双时间：本地拉取时刻必须落键并随快照下发（上游 built_at 另存）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    assert "sec:grype_db_updated_at" in inspect.getsource(sc._update_db)
    assert '"local_updated_at"' in inspect.getsource(sc.run_security_scan)


def test_security_stale_alert_declared():
    """告警接线与 runbook：stale + recovered 两个 code 都要有处置说明。"""
    import inspect
    from src.health_monitor import security_collector as sc
    from src.alert_notify.runbook import RUNBOOK
    s = inspect.getsource(sc.run_security_scan)
    assert "stale_decision(" in s and "_streak_read(" in s
    assert '"security.stale"' in s and '"security.recovered"' in s
    assert "sec:stale_alerted" in s and ">= 7" in s        # 7 轮升级 critical
    assert "security.stale" in RUNBOOK and "security.recovered" in RUNBOOK


def test_collector_uses_registry_not_raw_which():
    """扫描器探测只经注册入口——散落 which 会漏掉新增二进制/原因文案。"""
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc._collect_vulns)
    assert "detect_scanner()" in src
    assert 'shutil.which("grype")' not in src


def test_audit_marks_current_when_repo_has_package():
    """all_repos 必须进**写表**判定（不只展示）：仓库有包+无升级候选 ⇒ current。

    只接展示侧会出现「页面 current / 表里 unknown」两套口径（184 审核抓过的同类病）。
    """
    import inspect
    from src.scheduler import tasks
    s = inspect.getsource(tasks._dep_audit_impl)
    assert "_lat is None and nm in _repo" in s
    assert '"current", ""' in s


@needs_db
def test_finding_scanner_expanded_and_backfilled():
    """expand 步：scanner 列在且已回填；source 仍保留（双写期，contract 删列另批）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='security_finding'").fetchall()}
        n_null = conn.execute(
            "SELECT count(*) FROM security_finding WHERE scanner IS NULL").fetchone()[0]
    assert "scanner" in cols and "source" in cols, cols
    assert n_null == 0, f"{n_null} 行 scanner 未回填"


@needs_db
def test_ledger_gc_uses_three_days():
    from src.health_monitor import security_collector as sc
    src = inspect.getsource(sc.run_security_scan)
    assert "timedelta(days=3)" in src
    assert "timedelta(days=7)" not in src
