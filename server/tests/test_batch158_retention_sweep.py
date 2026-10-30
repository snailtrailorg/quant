"""批 158：滚动保留清道夫——NULL 否决 / source 匹配 / 分批 / 上限。"""
from __future__ import annotations


class _FakeCur:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _InnerConn:
    def __init__(self, plan):
        self.plan = plan
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        for frag, rc in self.plan:
            if frag in sql:
                return _FakeCur(rc)
        return _FakeCur(0)

    def commit(self):
        pass


class _CfgConn:
    def __init__(self, cfg_rows, veto_rows, inner):
        self.cfg_rows = cfg_rows
        self.veto_rows = veto_rows
        self.inner = inner

    def execute(self, sql, params=None):
        if sql.startswith("SELECT s.id"):
            rows = [tuple(r) for r in self.cfg_rows]
            c = _FakeCur(len(rows))
            c.fetchall = lambda: rows
            return c
        if sql.startswith("SELECT DISTINCT pg_table"):
            rows = [(v,) for v in self.veto_rows]
            c = _FakeCur(len(rows))
            c.fetchall = lambda: rows
            return c
        if sql.startswith("INSERT INTO sync_log"):
            return _FakeCur(1)
        return self.inner.execute(sql, params)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install(monkeypatch, plan, cfg_rows, veto_rows):
    import src.data_platform.db as DB
    inner = _InnerConn(plan)
    conn = _CfgConn(cfg_rows, veto_rows, inner)
    monkeypatch.setattr(DB, "get_conn", lambda: conn)
    return inner


def test_source_match_and_cutoff(monkeypatch):
    inner = _install(monkeypatch, [("DELETE FROM bar_1min", 3)],
                     [("binance_perp_1min", "bar_1min", "binance", 7)], [])
    from src.scheduler.tasks import bar_retention_sweep as _t
    res = _t.run()
    del_sqls = [s for s in inner.executed if s.startswith("DELETE")]
    assert del_sqls
    sql0 = del_sqls[0]
    assert "source=%s" in sql0
    # 步4盲审 B-P2-6：timestamptz 算术（now()-interval，天然 UTC）替代 AT TIME ZONE 'utc'（naive 会随会话漂移）
    assert "now() - interval '7 days'" in sql0
    assert "FOR UPDATE SKIP LOCKED" in sql0


def test_veto_skips_table(monkeypatch):
    inner = _install(monkeypatch, [("DELETE", 0)],
                     [("binance_perp_1min", "bar_1min", "binance", 7)], ["bar_1min"])
    from src.scheduler.tasks import bar_retention_sweep as _t
    res = _t.run()
    assert not [s for s in inner.executed if s.startswith("DELETE")]
    assert res["deleted"] == 0


def test_batching_stops_on_zero(monkeypatch):
    inner = _install(monkeypatch, [("DELETE FROM bar_15min", 0)],
                     [("binance_perp_15min", "bar_15min", "binance", 60)], [])
    from src.scheduler.tasks import bar_retention_sweep as _t
    res = _t.run()
    assert res["deleted"] == 0
    assert len([s for s in inner.executed if s.startswith("DELETE")]) == 1


def test_cap_breaks(monkeypatch):
    inner = _install(monkeypatch, [("DELETE FROM bar_1h", 50_000)],
                     [("binance_perp_hourly", "bar_1h", "binance", 180)], [])
    from src.scheduler.tasks import bar_retention_sweep as _t
    res = _t.run()
    assert res["deleted"] == 5_000_000


def test_retention_days_api_validation():
    """步4盲审 P1（A/B 同撞）：retention_days 入口闸——非 int/越界 4xx，防负值=全量删除按钮。"""
    from fastapi import HTTPException
    from src.web_api.routes.sync import update_sync_config_api  # noqa: F401  import 面
    import src.web_api.routes.sync as SR
    from src.web_api.errors import ApiError
    import inspect
    src_text = inspect.getsource(SR)
    # 结构断言：入口闸在 allowed 集之前、钳 1..3650
    assert "SYNC_RETENTION_DAYS_INVALID" in src_text
    assert "1 <= v <= 3650" in src_text
