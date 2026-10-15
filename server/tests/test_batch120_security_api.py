"""批 120 线② · 安全态势 API 端点（snapshot/history）。

覆盖（任务文件产出 5 + 指令）：
1. 404 结构化：无当日快照 ⇒ SECURITY_SNAPSHOT_NOT_FOUND（不静默空体）；
2. findings 停留天数计算 + 停留天数降序 + 已修复（last_seen < scan_date）沉底灰显标记；
3. 基线 diff 展示侧：与前一快照比三分类（port/package/config）；首日（无前快照）空 diff；
4. history component_changes 真算：前后 components jsonb 逐键版本差——版本变/未变两例
   + 非首次扫描未变组件不出现在 component_changes；
5. 权限钉：裸请求非 200（路由认证守门行为级）；
6. SQL 冻结守法：本路由文件无 get_conn/SQL 字面量（test_no_sql_in_new_routes 已扫，
   此处钉模块归属——security_store 才是 SQL 落点）。

mock：SQL 前缀分派（test_batch32 _dispatch 先例）——快照/前快照/findings 三查询
按 SQL 子串路由到构造行；基线 diff 走真 _baseline_diff（lock 加载 mock 锁定）。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

ADMIN = {"sub": 1, "username": "admin", "role": "admin", "db_role": "admin"}

DAY = date(2026, 10, 11)
PREV = date(2026, 10, 10)
FUTURE = date(2026, 10, 12)


def _dispatch(handlers):
    """SQL 前缀分派 mock（批32 先例）。handlers=[(子串, fetchall 行), ...] 按序首中。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn

    def _exec(sql, *a, **kw):
        for pat, rows in handlers:
            if pat in sql:
                cur = MagicMock()
                cur.fetchone.return_value = rows[0] if rows else None
                cur.fetchall.return_value = rows
                return cur
        raise AssertionError(f"unexpected SQL: {sql[:90]}")
    conn.execute = MagicMock(side_effect=_exec)
    return conn


def _ts():
    from datetime import datetime, timezone
    return datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)


def _snap_row(day=DAY, status="ok", pip=None, listening=None, ssh=None, summary=""):
    """security_snapshot 行（九列对齐 security_store._SNAP_COLS）。"""
    return (day, status,
            {"status": "ok", "pip": pip or {"flask": "3.0.0"}},
            {"status": "ok", "listening": listening or [{"addr": "0.0.0.0", "port": 22}]},
            {"status": "ok", "cfg": ssh or {"passwordauthentication": "no"}},
            {"status": "ok"}, {"status": "ok"}, summary or None, _ts())


def _finding_row(source="pip-audit", pkg="requests", vuln="GHSA-1", fix="2.32.0",
                 first=DAY, last=DAY, severity=None, reported_this_scan=True):
    """last_seen_at（批 124 同日重判）：缺省=本扫描时刻 06:00:30（晚于快照
    created_at=06:00 ⇒ active）；reported_this_scan=False ⇒ 05:59（早 ⇒ fixed
    ——模拟同日内"本轮未报"）。"""
    from datetime import datetime, timezone
    if reported_this_scan:
        last_at = datetime.combine(last, datetime.min.time(), tzinfo=timezone.utc).replace(hour=6, minute=0, second=30)
    else:
        last_at = datetime.combine(last, datetime.min.time(), tzinfo=timezone.utc).replace(hour=5, minute=59)
    return (source, pkg, vuln, fix, severity, first, last, last_at)


def _ledger_rows():
    """台账行（(source,name,version,origin,last_changed) fetchall——批 124 加 origin）。"""
    return [("pip", "requests", "2.31.0", "pip 安装", date(2026, 9, 1))]


def _prev_row(day=DAY):
    """get_prev_snapshot 行（三列：components/ports/ssh_cfg）。"""
    return (
        {"status": "ok", "pip": {"flask": "3.0.0"}},
        {"status": "ok", "listening": [{"addr": "0.0.0.0", "port": 22}]},
        {"status": "ok", "cfg": {"passwordauthentication": "no"}},
    )


_LOCK = {"flask": "3.0.0", "requests": "2.31.0"}


def _authed(client, conn):
    ctx = patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
        patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())), \
        patch("src.data_platform.db.get_conn", return_value=conn), \
        patch("src.health_monitor.security_collector._load_lock_packages", return_value=_LOCK)
    return ctx


# ---------------------------------------------------------------------------
# 1：404 结构化 + 参数校验
# ---------------------------------------------------------------------------

class TestSnapshot404:
    def test_no_snapshot_404_structured(self):
        from src.web_api.main import app
        client = TestClient(app)
        c = _dispatch([("FROM security_snapshot WHERE scan_date = %s", []),
                   ("FROM security_component", _ledger_rows())])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())), \
             patch("src.data_platform.db.get_conn", return_value=c):
            client.headers.update({"Authorization": "Bearer t"})
            r = client.get("/api/security/snapshot", params={"date": "20261011"})
        assert r.status_code == 404
        assert r.json()["code"] == "SECURITY_SNAPSHOT_NOT_FOUND"

    def test_bad_date_400(self):
        from src.web_api.main import app
        client = TestClient(app)
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())):
            client.headers.update({"Authorization": "Bearer t"})
            r = client.get("/api/security/snapshot", params={"date": "2026-10-11"})
        assert r.status_code == 400 and r.json()["code"] == "SECURITY_DATE_INVALID"

    def test_no_param_defaults_to_today(self):
        """**反证钉（P0-2）**：无参（前端唯一入口形态）⇒ 默认今日 + 当日快照存在 ⇒ 200。

        旧签名参数名 `date` 遮蔽 `datetime.date`，无参路径 `date.today()` 炸 500——
        参数若改回 `date` 名（不走 alias）此用例必红。
        """
        from src.web_api.main import app
        client = TestClient(app)
        today = date.today()
        c = _dispatch([
            ("WHERE scan_date = %s", [_snap_row(day=today)]),
            ("ORDER BY scan_date DESC LIMIT 1", []),
            ("FROM security_finding WHERE first_seen <= %s", []),
            ("FROM security_component", _ledger_rows()),
        ])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())), \
             patch("src.data_platform.db.get_conn", return_value=c), \
             patch("src.health_monitor.security_collector._load_lock_packages", return_value=_LOCK):
            client.headers.update({"Authorization": "Bearer t"})
            r = client.get("/api/security/snapshot")
        assert r.status_code == 200
        assert r.json()["scan_date"] == today.isoformat()


# ---------------------------------------------------------------------------
# 2：findings 排序 + 停留天数 + fixed 标记
# ---------------------------------------------------------------------------

class TestFindingsOrder:
    def test_dwell_days_desc_and_fixed_sinks(self):
        from src.web_api.main import app
        client = TestClient(app)
        # 三条活跃（停留 3/1/0 天）+ 一条已修复（last_seen=PREV < DAY）
        findings = [
            _finding_row(pkg="c", vuln="V3", first=date(2026, 10, 11)),             # 0 天
            _finding_row(pkg="a", vuln="V1", first=date(2026, 10, 8)),               # 3 天
            _finding_row(pkg="fixed-pkg", vuln="VX", first=date(2026, 10, 9),
                         last=PREV),   # last_seen=PREV < DAY ⇒ 日粒度已 fixed
            _finding_row(pkg="b", vuln="V2", first=date(2026, 10, 10)),             # 1 天
        ]
        c = _dispatch([
            ("WHERE scan_date = %s", [_snap_row()]),
            ("ORDER BY scan_date DESC LIMIT 1", [_prev_row()]),
            ("FROM security_finding WHERE first_seen <= %s", findings),
            ("FROM security_component", _ledger_rows()),
        ])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())), \
             patch("src.data_platform.db.get_conn", return_value=c), \
             patch("src.health_monitor.security_collector._load_lock_packages", return_value=_LOCK):
            client.headers.update({"Authorization": "Bearer t"})
            r = client.get("/api/security/snapshot", params={"date": "20261011"})
        assert r.status_code == 200
        d = r.json()
        pkgs = [f["pkg"] for f in d["findings"]]
        # 已修复沉底；活跃按停留天数降序
        assert pkgs == ["a", "b", "c", "fixed-pkg"]
        assert d["findings"][0]["dwell_days"] == 3
        assert d["findings"][2]["dwell_days"] == 0
        assert d["findings"][3]["fixed"] is True
        assert all(f["fixed"] is False for f in d["findings"][:3])

    def test_degraded_day_nothing_marked_fixed(self):
        """**反证钉（P1-2）**：当日快照 degraded ⇒ 任何 finding 不判 fixed。

        降级日采集侧不推 last_seen（_upsert_findings 仅 ok 分支）——last_seen<day
        只是旧值不是「已修复」证据；判 fixed=假清零。撤 vulns_ok 参数必红。
        """
        from src.web_api.main import app
        client = TestClient(app)
        findings = [
            _finding_row(pkg="old", vuln="V1", first=date(2026, 10, 8), last=PREV),
        ]
        c = _dispatch([
            ("WHERE scan_date = %s", [_snap_row(status="degraded")]),
            ("ORDER BY scan_date DESC LIMIT 1", []),
            ("FROM security_finding WHERE first_seen <= %s", findings),
            ("FROM security_component", _ledger_rows()),
        ])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())), \
             patch("src.data_platform.db.get_conn", return_value=c), \
             patch("src.health_monitor.security_collector._load_lock_packages", return_value=_LOCK):
            client.headers.update({"Authorization": "Bearer t"})
            r = client.get("/api/security/snapshot", params={"date": "20261011"})
        assert r.status_code == 200
        assert all(f["fixed"] is False for f in r.json()["findings"])


# ---------------------------------------------------------------------------
# 3：基线 diff 三分类 + 首日空 diff
# ---------------------------------------------------------------------------

class TestBaselineDiff:
    def test_diff_three_buckets(self):
        from src.web_api.main import app
        client = TestClient(app)
        c = _dispatch([
            ("WHERE scan_date = %s", [_snap_row(
                pip={"flask": "3.0.0", "mystery": "1.0"},
                listening=[{"addr": "0.0.0.0", "port": 22}, {"addr": "0.0.0.0", "port": 8080}],
                ssh={"passwordauthentication": "yes"})]),
            ("ORDER BY scan_date DESC LIMIT 1", [_prev_row()]),   # 前日：无 8080/无 mystery/pwd=no
            ("FROM security_finding WHERE first_seen <= %s", []),
            ("FROM security_component", _ledger_rows()),
        ])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())), \
             patch("src.data_platform.db.get_conn", return_value=c), \
             patch("src.health_monitor.security_collector._load_lock_packages", return_value=_LOCK):
            client.headers.update({"Authorization": "Bearer t"})
            r = client.get("/api/security/snapshot", params={"date": "20261011"})
        bd = r.json()["baseline_diff"]
        assert any("8080" in x for x in bd["new_ports"])
        assert any("mystery" in x for x in bd["new_packages"])
        assert any("passwordauthentication" in x for x in bd["cfg_flips"])

    def test_first_day_empty_diff(self):
        from src.web_api.main import app
        client = TestClient(app)
        c = _dispatch([
            ("WHERE scan_date = %s", [_snap_row()]),
            ("ORDER BY scan_date DESC LIMIT 1", []),   # 无前快照=首日
            ("FROM security_finding WHERE first_seen <= %s", []),
            ("FROM security_component", _ledger_rows()),
        ])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())), \
             patch("src.data_platform.db.get_conn", return_value=c), \
             patch("src.health_monitor.security_collector._load_lock_packages", return_value=_LOCK):
            client.headers.update({"Authorization": "Bearer t"})
            r = client.get("/api/security/snapshot", params={"date": "20261011"})
        assert r.json()["baseline_diff"] == {"new_ports": [], "new_packages": [], "cfg_flips": []}


# ---------------------------------------------------------------------------
# 4：history component_changes 真算（版本变/未变两例）
# ---------------------------------------------------------------------------

class TestHistory:
    def _run_history(self):
        from src.web_api.main import app
        client = TestClient(app)
        snaps = [
            _snap_row(day=PREV, pip={"flask": "3.0.0", "requests": "2.31.0"}),
            # requests 升版（变）；flask 不动（未变）；nginx None→版本（顶层键变）
            _snap_row(day=DAY, pip={"flask": "3.0.0", "requests": "2.32.0"}),
        ]
        snaps[1] = (snaps[1][0], snaps[1][1],
                    {**snaps[1][2], "nginx": "1.24.0"}, *snaps[1][3:])
        c = _dispatch([
            ("ORDER BY scan_date", snaps),                    # get_snapshots_recent（升序）
            # get_finding_spans 仅两列 (first_seen, last_seen)
            ("SELECT first_seen, last_seen FROM security_finding",
             [(date(2026, 10, 10), FUTURE), (date(2026, 10, 11), FUTURE)]),
        ])
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.auth.load_effective_permissions", return_value=({"read"}, set())), \
             patch("src.data_platform.db.get_conn", return_value=c), \
             patch("src.health_monitor.security_collector._load_lock_packages", return_value=_LOCK):
            client.headers.update({"Authorization": "Bearer t"})
            return client.get("/api/security/history", params={"days": 30})

    def test_component_changes_version_flip_detected(self):
        r = self._run_history()
        assert r.status_code == 200
        items = r.json()["items"]
        by_date = {i["scan_date"]: i for i in items}
        assert "requests" in by_date["2026-10-11"]["component_changes"]
        assert "nginx" in by_date["2026-10-11"]["component_changes"]

    def test_component_changes_stable_not_listed(self):
        r = self._run_history()
        by_date = {i["scan_date"]: i for i in r.json()["items"]}
        # flask 两天同版 ⇒ 不在变更集；首日（无前快照）component_changes 恒空
        assert "flask" not in by_date["2026-10-11"]["component_changes"]
        assert by_date["2026-10-10"]["component_changes"] == []

    def test_vuln_counts_per_day(self):
        r = self._run_history()
        by_date = {i["scan_date"]: i for i in r.json()["items"]}
        # spans：(10-10~FUT)、(10-11~FUT) ⇒ 10-11 当日 vuln_count=2/new=1；10-10 vuln=1/new=1
        assert by_date["2026-10-11"]["vuln_count"] == 2
        assert by_date["2026-10-11"]["new_vulns"] == 1
        assert by_date["2026-10-10"]["vuln_count"] == 1
        assert by_date["2026-10-10"]["new_vulns"] == 1


# ---------------------------------------------------------------------------
# 5：权限钉 + SQL 落点钉
# ---------------------------------------------------------------------------

def test_snapshot_rejects_unauthenticated():
    from src.web_api.main import app
    client = TestClient(app)
    r = client.get("/api/security/snapshot")
    assert r.status_code != 200 and r.status_code in (401, 403, 422)
    r2 = client.get("/api/security/history")
    assert r2.status_code != 200 and r2.status_code in (401, 403, 422)


def test_route_file_has_no_sql_and_store_is_the_sink():
    from pathlib import Path
    route_src = Path("src/web_api/routes/security.py").read_text(encoding="utf-8")
    assert "get_conn" not in route_src
    import re as _re
    sql_re = _re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", _re.I)
    lit = [ln for ln in route_src.splitlines() if sql_re.match(ln)]
    assert not lit, f"路由文件不得内联 SQL: {lit[:3]}"
    store_src = Path("src/data_platform/security_store.py").read_text(encoding="utf-8")
    assert "security_snapshot" in store_src and "security_finding" in store_src
