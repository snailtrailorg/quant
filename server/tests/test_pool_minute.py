"""池驱动分钟线同步测试（S+T 审修订版语义）。"""
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import pytest


class TestGetRateLimit:
    def test_params_override(self):
        from src.data_platform.data_source import TushareDataSource
        ds = TushareDataSource(params='{"rate_limits": {"stk_mins": 60}}')
        assert ds.get_rate_limit("stk_mins") == 60.0
        assert ds.get_rate_limit("daily") == 0.5    # 未覆盖回落默认

    def test_no_params_falls_back_to_class_defaults(self):
        from src.data_platform.data_source import TushareDataSource
        ds = TushareDataSource()
        assert ds.get_rate_limit("stk_mins") == 0.15
        assert ds.get_rate_limit("ghost_api") == 0.0   # 未知接口不限

    def test_invalid_value_tolerated(self):
        from src.data_platform.data_source import TushareDataSource
        ds = TushareDataSource(params='{"rate_limits": {"stk_mins": "bad"}}')
        assert ds.get_rate_limit("stk_mins") == 0.15   # 非法回落默认


class TestVtToTs:
    def test_roundtrip(self):
        from src.data_platform.schema import vt_to_ts, to_vt_symbol
        for vt in ["600000.SHSE", "000001.SZSE", "113000.SHSE", "830001.BSE"]:
            assert to_vt_symbol(vt_to_ts(vt)) == vt

    def test_no_dot_passthrough(self):
        from src.data_platform.schema import vt_to_ts
        assert vt_to_ts("600000") == "600000"


class TestStkMinsGate:
    def test_gate_passes_when_free(self):
        """闸门空闲（SET NX 成功）→ 立即返回。"""
        from src.data_sync.pool_minute import _stk_mins_gate
        r = MagicMock()
        r.set.return_value = True
        _stk_mins_gate(r)   # 不抛即过

    def test_gate_times_out(self):
        """闸门持续被占 → 超时抛 RateLimited。"""
        from src.data_sync.pool_minute import _stk_mins_gate, RateLimited
        r = MagicMock()
        r.set.return_value = False   # 永远拿不到 token
        with pytest.raises(RateLimited):
            _stk_mins_gate(r, timeout_s=1)


class TestPoolSymbolsWithGap:
    def test_no_data_symbol_uses_pool_start(self):
        """无数据标的 → 从池起始日开始拉。"""
        from src.data_sync.pool_minute import _pool_symbols_with_gap
        from datetime import date
        conn = MagicMock()
        conn.__enter__.return_value = conn
        # 第一次查池内标的，第二次查 bar_1min max(ts)
        conn.execute.side_effect = [
            MagicMock(fetchall=lambda: [("600000.SHSE",)]),
            MagicMock(fetchall=lambda: []),   # 无数据
        ]
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            gaps = _pool_symbols_with_gap("p1", date(2026, 7, 1), date(2026, 8, 19))
        assert len(gaps) == 1
        assert gaps[0][0] == "600000.SHSE"
        assert gaps[0][1] == "600000.SH"     # ts 格式（S-F1 归一）
        assert gaps[0][2] == date(2026, 7, 1)

    def test_fresh_symbol_no_gap(self):
        """最后 ts 在昨天及以后 → 无缺口（零 API）。"""
        from src.data_sync.pool_minute import _pool_symbols_with_gap
        from datetime import date, datetime
        conn = MagicMock()
        conn.__enter__.return_value = conn
        fresh = datetime(2026, 8, 19, 10, 0)
        conn.execute.side_effect = [
            MagicMock(fetchall=lambda: [("600000.SHSE",)]),
            MagicMock(fetchall=lambda: [("600000.SHSE", fresh)]),
        ]
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            gaps = _pool_symbols_with_gap("p1", date(2026, 7, 1), date(2026, 8, 19))
        assert gaps == []


class TestSyncPoolsMinuteWiring:
    def test_idle_when_no_pools_configured(self):
        """无配置分钟历史的 A 股池 → idle（零 API 零浪费）。"""
        from src.data_sync import pool_minute as pm
        from src.data_sync.sync_lock import SyncLock
        fake_lock = MagicMock()
        fake_lock.__enter__ = MagicMock(return_value=fake_lock)
        fake_lock.__exit__ = MagicMock(return_value=False)
        fake_lock.acquired = True
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = []
        import src.data_platform.db as db
        with patch.object(pm, "SyncLock", return_value=fake_lock), \
             patch.object(db, "get_conn", return_value=conn):
            r = pm.sync_pools_minute()
        assert r["status"] == "idle"
