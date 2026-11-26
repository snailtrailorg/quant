"""批 190 · 安全三任务失败隔离与互斥。

钉三条用户要求：
1. 失败不覆盖上一次成功结果（scan 快照 / grype 状态 / dep 判定与轮次锚）；
2. 定时与手动互斥由任务入口 SET NX 保证（路由只预检）；
3. 失败事实必须独立可定位（sec:scan_last_attempt / last_attempt）。
"""
from __future__ import annotations

import contextlib
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class _BR:
    """最小 Valkey 桩：SET NX/EX、GET(bytes 兼容)、DELETE、MGET。"""
    def __init__(self, store=None):
        self.store = dict(store or {})

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None, nx=False):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def delete(self, k):
        self.store.pop(k, None)

    def mget(self, keys):
        return [self.store.get(k) for k in keys]


def _fake_conn(has_ok: bool):
    """get_conn 桩：prev_row=None；ok 行存在性由 has_ok 控制；记录 SQL。"""
    calls = []

    class _Cur:
        def __init__(self):
            self._rows = []

        def execute(self, sql, params=None):
            calls.append((sql, params))
            s = " ".join(sql.split())
            if "WHERE status='ok'" in s:
                return MagicMock(fetchone=lambda: (1,) if has_ok else None,
                                 fetchall=lambda: [])
            if "FROM security_snapshot" in s and "scan_date <" in s:
                return MagicMock(fetchone=lambda: None, fetchall=lambda: [])
            return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

        def executemany(self, sql, rows):
            calls.append((sql, rows))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    conn = MagicMock()
    conn.execute.side_effect = _Cur().execute
    conn.commit = lambda: None
    conn.cursor.return_value = _Cur()
    return conn, calls


@contextlib.contextmanager
def _conn_ctx(conn):
    yield conn


def _run_scan(monkeypatch, has_ok: bool):
    """全链跑 run_security_scan：五分区 ok、vulns degraded（最危险的假清零入口）。"""
    import src.health_monitor.security_collector as sc
    import src.data_platform.db as DB

    br = _BR()
    conn, calls = _fake_conn(has_ok)
    monkeypatch.setattr("src.quant_common.redis_client.business_redis", lambda **kw: br)
    monkeypatch.setattr(DB, "get_conn", lambda: _conn_ctx(conn))
    for name, val in (("_collect_components", {"status": "ok", "pip": {}}),
                      ("_collect_ssh", {"status": "ok"}),
                      ("_collect_ports", {"status": "ok"}),
                      ("_collect_dnf", {"status": "ok"}),
                      ("_collect_headers", {"status": "ok"}),
                      ("_collect_vulns", {"status": "degraded",
                                          "degraded_reason": "grype 缺失"})):
        monkeypatch.setattr(sc, name, lambda val=val: dict(val))
    monkeypatch.setattr(sc, "_baseline_diff", lambda *a, **k: [])
    monkeypatch.setattr(sc, "_backfill_descriptions", lambda *a, **k: 0)
    monkeypatch.setattr(sc, "_ai_summary", lambda *a, **k: "")
    monkeypatch.setattr(sc, "_alert", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_streak_read", lambda *a, **k: 0)
    monkeypatch.setattr(sc, "stale_decision", lambda prev, deg, alerted: (prev, None))
    return sc.run_security_scan(today=date(2026, 11, 26)), calls, br


class TestScanIsolation:
    def test_degraded_does_not_overwrite_success_snapshot(self, monkeypatch):
        """已有 ok 快照 + 本轮 vulns degraded：不得写快照（created_at 不推进=不假清零）。"""
        out, calls, br = _run_scan(monkeypatch, has_ok=True)
        assert out["status"] == "degraded"
        assert not any("INSERT INTO security_snapshot" in " ".join(s.split())
                       for s, _ in calls)
        att = json.loads(br.store["sec:scan_last_attempt"])
        assert att["status"] == "degraded" and att["degraded_zones"]

    def test_degraded_without_any_ok_row_still_lands_for_diagnosis(self, monkeypatch):
        """从未成功过：落 degraded 行供诊断（无旧结果可破坏）。"""
        out, calls, _ = _run_scan(monkeypatch, has_ok=False)
        assert out["status"] == "degraded"
        assert any("INSERT INTO security_snapshot" in " ".join(s.split())
                   for s, _ in calls)

    def test_task_entry_lock_is_authoritative(self):
        """beat/手动同一入口：锁占用 -> busy，不执行采集。"""
        import src.scheduler.tasks as T
        br = _BR({"sec:scan_lock": "other"})
        with patch("src.quant_common.redis_client.business_redis", lambda **kw: br), \
             patch("src.health_monitor.security_collector.run_security_scan") as run:
            out = T.security_scan.run()
        assert out == {"status": "busy"} and not run.called


class TestGrypeStateLayering:
    def test_failure_keeps_last_success_and_records_attempt(self, monkeypatch):
        from src.health_monitor import security_collector as sc
        br = _BR({"sec:grype_db_state": json.dumps(
            {"ts": "2026-11-25T00:00:00+00:00", "state": "updated", "error": ""})})
        monkeypatch.setattr("src.quant_common.redis_client.business_redis", lambda **kw: br)
        monkeypatch.setattr(sc, "_update_db", lambda env: "update-failed")
        out = sc.update_grype_db()
        assert out["state"] == "update-failed"          # 返回值仍供任务层告警
        st = json.loads(br.store["sec:grype_db_state"])
        assert st["state"] == "updated"                 # 有效态不被失败冲掉
        assert st["last_attempt"]["state"] == "update-failed"

    def test_first_ever_failure_becomes_effective_state(self, monkeypatch):
        from src.health_monitor import security_collector as sc
        br = _BR()
        monkeypatch.setattr("src.quant_common.redis_client.business_redis", lambda **kw: br)
        monkeypatch.setattr(sc, "_update_db", lambda env: "update-failed")
        sc.update_grype_db()
        st = json.loads(br.store["sec:grype_db_state"])
        assert st["state"] == "update-failed"


def _dep_env(monkeypatch, versions):
    """dep_audit 全链桩：2 pip 包（versions 决定哪包抛错）+ 空 OS 域。"""
    import src.scheduler.tasks as T
    import src.health_monitor.os_pkg as OP
    import src.data_platform.db as DB

    br = _BR()
    monkeypatch.setattr("src.quant_common.redis_client.business_redis", lambda **kw: br)
    monkeypatch.setattr(OP, "pip_installed", lambda: [
        {"name": "p_ok", "cur": "1.0", "local": False},
        {"name": "p_bad", "cur": "1.0", "local": False}])

    def _pv(name, timeout=10):
        if name == "p_bad":
            raise RuntimeError("network down")
        return {"versions": ["1.0", "2.0"], "yanked": []}
    monkeypatch.setattr(OP, "pypi_versions_cached", _pv)
    monkeypatch.setattr(OP, "detect_provider", lambda: None)

    calls = []

    class _Cur:
        def executemany(self, sql, rows):
            calls.append((sql, list(rows)))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            return MagicMock(fetchall=lambda: [], fetchone=lambda: None)

    conn = MagicMock()
    conn.cursor.return_value = _Cur()
    conn.commit = lambda: None
    monkeypatch.setattr(DB, "get_conn", lambda: _conn_ctx(conn))
    return T, calls, br


class TestDepAuditIsolation:
    def test_failed_package_keeps_previous_judgement(self, monkeypatch):
        T, calls, _ = _dep_env(monkeypatch, None)
        out = T.dep_audit.run()
        assert out["status"] == "partial"
        upserts = [r for s, rows in calls if "judge_class=EXCLUDED.judge_class" in s
                   for r in rows]
        assert [r[0] for r in upserts] == ["p_ok"]          # 失败包不进判定 upsert
        keeps = [r for s, rows in calls if "DO UPDATE SET last_seen=now()" in s
                 and "judge_class" not in s for r in rows]
        assert [r[0] for r in keeps] == ["p_bad"]           # 只推 last_seen

    def test_partial_round_does_not_advance_last_complete(self, monkeypatch):
        T, _, br = _dep_env(monkeypatch, None)
        T.dep_audit.run()
        contract = json.loads(br.store["sec:dep_audit"])
        assert "last_complete" not in contract

    def test_zombie_lock_is_reclaimable(self):
        """锁在且无心跳、锁龄>10min=僵尸（SIGKILL 无 finally）——重抢，不 48h 死锁。"""
        import datetime as dt
        import src.scheduler.tasks as T
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).isoformat()
        br = _BR({"sec:dep_audit_lock": old + "-deadbeef"})
        with patch("src.quant_common.redis_client.business_redis", lambda **kw: br), \
             patch("src.scheduler.tasks._dep_audit_impl",
                   return_value={"status": "ok"}) as impl:
            out = T.dep_audit.run()
        assert out == {"status": "ok"} and impl.called

    def test_lock_prevents_concurrent_manual_and_beat(self):
        import src.scheduler.tasks as T
        br = _BR({"sec:dep_audit_lock": "running"})
        with patch("src.quant_common.redis_client.business_redis", lambda **kw: br), \
             patch("src.scheduler.tasks._dep_audit_impl") as impl:
            out = T.dep_audit.run()
        assert out == {"status": "busy"} and not impl.called
