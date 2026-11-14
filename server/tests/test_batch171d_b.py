"""批 171d-B · 地板全局化守门（`flow/任务/批171d-B-地板全局化.md` §3 验收）。

行为级断言：四源 max 三例 / policy_discard 单源单段（无重叠双段）/ clamped 两维正交 /
互斥两端点 400 / 坏值 fail-open 留痕。
"""
from datetime import date

import pytest


class TestFourSourceMax:

    def _wf(self, monkeypatch, inception, ret_s, src_lo, floor_default):
        from src.data_sync import engine as eng
        monkeypatch.setattr(eng, "sm_inception", lambda sym: inception)
        monkeypatch.setattr(eng, "get_conn", _NoConn)
        cfg = {"_floor_default": floor_default, "retention": ret_s, "id": "x"}

        class _A:
            provider = "tushare"

            def available_range(self, kind):
                return (src_lo, None)

        return eng._window_floors(_A(), "bar_daily", cfg)

    def test_null_retention_global_wins(self, monkeypatch):
        floor, _ = self._wf(monkeypatch, None, None, "2010-01-01", date(2014, 1, 1))
        assert floor == date(2014, 1, 1)

    def test_deep_retention_still_maxed(self, monkeypatch):
        """max 语义直证（D31 v2.4）：astock_basic 型（retention 1990 深于全局）⇒ 2014。"""
        floor, _ = self._wf(monkeypatch, None, date(1990, 12, 19), "1991-01-01", date(2014, 1, 1))
        assert floor == date(2014, 1, 1), "深史 retention 被 max 顶住（回落链已废弃）"

    def test_inception_wins_per_symbol(self, monkeypatch):
        """per-symbol 直参 inception（测试助手 _wf 是家族级 sym=None 路径——inception
        经 kwarg 传才进 known dict）。"""
        from src.data_sync import engine as eng
        monkeypatch.setattr(eng, "get_conn", _NoConn)
        cfg = {"_floor_default": date(2014, 1, 1), "retention": None, "id": "x"}

        class _A:
            provider = "tushare"

            def available_range(self, kind):
                return ("2010-01-01", None)

        floor, _ = eng._window_floors(_A(), "bar_daily", cfg, sym="X.XSHG",
                                      inception="2030-01-01")
        assert floor == date(2030, 1, 1)

    def test_policy_discard_single_segment_global(self, monkeypatch):
        """NULL retention+全局 2014+源 2010 ⇒ 恰一段 [2010-01-01, 2014-01-01) reason 全局。"""
        _, excl = self._wf(monkeypatch, None, None, "2010-01-01", date(2014, 1, 1))
        pd = [e for e in excl if e["kind"] == "policy_discard"]
        assert len(pd) == 1
        assert pd[0]["from"] == "2010-01-01" and pd[0]["to"] == "2014-01-01"
        assert "全局地板" in pd[0]["reason"]

    def test_policy_discard_single_segment_retention(self, monkeypatch):
        """retention 2020+全局 2014+源 2005 ⇒ 恰一段 [2005, 2020) reason 任务——**无重叠双段**。"""
        _, excl = self._wf(monkeypatch, None, date(2020, 1, 1), "2005-01-01", date(2014, 1, 1))
        pd = [e for e in excl if e["kind"] == "policy_discard"]
        assert len(pd) == 1, "单源判据：防双臂各记撞 UNIQUE"
        assert pd[0]["to"] == "2020-01-01" and "任务 retention" in pd[0]["reason"]


class _NoConn:
    def __enter__(self):
        raise RuntimeError("no db in unit test")

    def __exit__(self, *a):
        return False


class TestFloorDefaultRead:

    def test_bad_value_fail_open_with_warning(self, caplog):
        """验收 6：'abc' ⇒ None+warning（fail-open 留痕）。"""
        import logging
        from src.data_sync import engine as eng
        with pytest.MonkeyPatch.context() as mp:
            class _C:
                def execute(self, sql, p=None):
                    return self

                def fetchone(self):
                    return ("abc",)

            class _Conn:
                def __enter__(self):
                    return _C()

                def __exit__(self, *a):
                    return False

            mp.setattr(eng, "get_conn", lambda *a, **k: _Conn())
            with caplog.at_level(logging.WARNING, logger="src.data_sync.engine"):
                v = eng._floor_default()
        assert v is None
        assert any("sync_floor_default" in r.getMessage() for r in caplog.records)

    def test_no_key_row_returns_none(self, monkeypatch):
        from src.data_sync import engine as eng
        with pytest.MonkeyPatch.context() as mp:
            class _C:
                def execute(self, sql, p=None):
                    return self

                def fetchone(self):
                    return None

            class _Conn:
                def __enter__(self):
                    return _C()

                def __exit__(self, *a):
                    return False

            mp.setattr(eng, "get_conn", lambda *a, **k: _Conn())
            assert eng._floor_default() is None


class TestClampedOrthogonal:
    """两维正交：floor 夹起点（span 不截）也出具——mock 站点形态。"""

    def test_floor_only_emits_clamped(self, monkeypatch):
        from src.data_sync import engine as eng
        from datetime import date as _d
        monkeypatch.setattr(eng, "_read_sync_kind", lambda sid: {
            "kind": "suspend", "sub_kind": None, "pg_table": "suspend_calendar",
            "pk_cols": "{ts_code,trade_date}", "float_cols": "{}",
            "text_cols": "{suspend_type,full_day}", "rebuild": "incremental"})
        monkeypatch.setattr(eng, "_family_start",
                            lambda *a, **k: (_d(2014, 1, 1), []))
        monkeypatch.setattr(eng, "_data_ready_end_date", lambda lag=0: "20140601")
        # 地板 2014 夹起点（backfill 2010→2014）但 span 366 >> 窗 ⇒ span 不截

        class _FD:
            empty = True

        class _FA:
            provider = "tushare"
            venue = ""

            def fetch_supply(self, *a, **k):
                return _FD()

        monkeypatch.setattr(eng, "_get_supply_adapter", lambda cfg: _FA())
        monkeypatch.setattr(eng, "get_conn", _NoConn)
        cfg = {"id": "suspend_d_sync", "mode": "incremental", "enabled": True,
               "last_sync_date": None, "provider": "tushare", "_max_span_days": 366}
        r = eng._HANDLERS["suspend_d_sync"](cfg, "20140601", backfill_from="20100101")
        assert r["clamped"]["floor"] == "20140101" and r["clamped"]["span"] is False


class TestMutexEndpoints:

    def test_trigger_rejects_inside_prune_window(self, monkeypatch):
        """验收 5：retention_days=60 + 回补 90 天前 ⇒ 400 SYNC_BACKFILL_RETENTION_CONFLICT。"""
        from src.web_api.routes import sync as sync_routes
        from src.web_api.errors import ApiError
        with pytest.MonkeyPatch.context() as mp:
            class _C:
                def execute(self, sql, p=None):
                    return self

                def fetchone(self):
                    return (60,)

            class _Conn:
                def __enter__(self):
                    return _C()

                def __exit__(self, *a):
                    return False

            mp.setattr("src.data_platform.db.get_conn", lambda *a, **k: _Conn())
            from datetime import date, timedelta
            old = (date.today() - timedelta(days=90)).strftime("%Y%m%d")
            with pytest.raises(ApiError) as ei:
                sync_routes._validate_backfill_retention("binance_perp_1min", old)
        assert ei.value.code == "SYNC_BACKFILL_RETENTION_CONFLICT"

    def test_null_retention_days_passes(self, monkeypatch):
        from src.web_api.routes import sync as sync_routes
        with pytest.MonkeyPatch.context() as mp:
            class _C:
                def execute(self, sql, p=None):
                    return self

                def fetchone(self):
                    return (None,)

            class _Conn:
                def __enter__(self):
                    return _C()

                def __exit__(self, *a):
                    return False

            mp.setattr("src.data_platform.db.get_conn", lambda *a, **k: _Conn())
            sync_routes._validate_backfill_retention("astock_daily", "20100101")  # 不抛
