"""批 100 · 供给面分派键迁移（研究稿 §九 必修①）。

验收四点：
1. `_SUPPLY_PULL` 覆盖引擎 9 个工厂项的 `(kind, sub_kind)`（tier1 7 + 全量重建 2）——无遗漏；
2. adapter 端口 `fetch_supply` 按 `(kind, sub_kind)` 正确分派到 tushare_adapter 的 pull 函数；
3. **消费者断言**：`sync_config.provider` 对那 9 项**真生效**——注册替身 provider ⇒ 引擎改走它；
4. 反证：把 `_get_supply_adapter` 回退成硬编码 tushare（=旧行为）⇒ 替身 provider 断言必红。
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


class _FakeDS:
    provider = "test"

    def record_usage(self, **kw):
        return None

    def get_rate_limit(self, api_name):
        return 0.0


# ---------------------------------------------------------------------------
# 1 + 2：映射完备性 + 分派正确性
# ---------------------------------------------------------------------------

class TestSupplyMap:
    def test_covers_all_factory_items(self):
        """`_SUPPLY_PULL` 键集 == 引擎 `_TIER1_BATCH`/`_TIER1_FULL` 的 (kind, sub_kind) 并集。"""
        from src.data_platform.adapters.base import _SUPPLY_PULL
        from src.data_sync import engine
        engine_keys = {(k, s) for (k, s, _t, _p) in engine._TIER1_BATCH.values()}
        engine_keys |= {(k, s) for (k, s, _t, _p, _d) in engine._TIER1_FULL.values()}
        assert len(engine_keys) == 9, f"工厂项应为 9（tier1 7 + full 2），实得 {len(engine_keys)}"
        assert set(_SUPPLY_PULL) == engine_keys

    def test_all_pull_functions_exist(self):
        """每个映射值的 pull 函数在 tushare_adapter 里真实存在。"""
        from src.data_platform.adapters import tushare_adapter as ta
        from src.data_platform.adapters.base import _SUPPLY_PULL
        for key, fn_name in _SUPPLY_PULL.items():
            assert callable(getattr(ta, fn_name, None)), f"{key} → {fn_name} 不存在"


class TestFetchSupplyDispatch:
    def _adapter(self):
        with patch("src.data_platform.data_source.get_data_source", return_value=_FakeDS()):
            from src.data_platform.adapters.base import get_adapter
            return get_adapter("tushare")

    def test_dispatch_trade_date_and_ann_date(self):
        """按 (kind, sub_kind) 分派；date_param 语义对（forecast 走 ann_date、其余 trade_date）。"""
        calls = []

        def _spy(name):
            def _f(trade_date=None, end_date=None, ann_date=None):
                kw = {k: v for k, v in (("trade_date", trade_date),
                                        ("ann_date", ann_date)) if v is not None}
                calls.append((name, kw))
                return pd.DataFrame()
            return _f

        ad = self._adapter()
        with patch("src.data_platform.adapters.tushare_adapter.pull_top_list", new=_spy("top_list")), \
             patch("src.data_platform.adapters.tushare_adapter.pull_forecast", new=_spy("forecast")):
            ad.fetch_supply("featured_daily", "top_list", trade_date="20260930")
            ad.fetch_supply("financial_stmt", "forecast", ann_date="20260930")
        assert calls[0] == ("top_list", {"trade_date": "20260930"})
        assert calls[1] == ("forecast", {"ann_date": "20260930"})

    def test_unknown_key_raises(self):
        """未声明的 (kind, sub_kind) 响亮拒绝（不静默按 tushare 拉数）。"""
        from src.data_platform.adapters.base import UnsupportedFeature
        ad = self._adapter()
        with pytest.raises(UnsupportedFeature):
            ad.fetch_supply("featured_daily", "cyq_chips")   # 池内表，非本端口域

    def test_namechange_drops_window(self):
        """快照表（namechange）不吃窗口参数——引擎传统一形态，adapter 侧过滤掉。"""
        seen = {}

        def _f(**kw):
            seen.update(kw)
            return pd.DataFrame()

        ad = self._adapter()
        with patch("src.data_platform.adapters.tushare_adapter.pull_namechange", new=_f):
            ad.fetch_supply("static_list", "namechange", trade_date="20260930")
        assert seen == {}, f"namechange 不该收到窗口参数: {seen}"


# ---------------------------------------------------------------------------
# 3：消费者断言 —— provider 真生效（核心）
# ---------------------------------------------------------------------------

_SPY_CALLS: list = []


def _make_spy_adapter_cls():
    from src.data_platform.adapters.base import BaseDataAdapter

    class _Cls(BaseDataAdapter):
        provider = "spy100"

        def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
            return pd.DataFrame()

        def pull_minute(self, symbol, freq, start, end):
            return pd.DataFrame()

        def to_bar_rows(self, df, freq, adj_map=None):
            return []

        def fetch_supply(self, kind, sub_kind=None, **params):
            _SPY_CALLS.append((kind, sub_kind, params))
            return pd.DataFrame()

    return _Cls


class TestProviderEffective:
    """`sync_config.provider` 对工厂项真生效（批 100 的核心目标）。"""

    def test_factory_routes_to_declared_provider(self):
        from src.data_platform.adapters.base import _ADAPTERS, register_adapter
        from src.data_sync import engine
        _SPY_CALLS.clear()
        cls = _make_spy_adapter_cls()
        register_adapter(cls)
        try:
            with patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
                 patch.object(engine, "_data_ready_end_date", return_value="20261001"), \
                 patch.object(engine, "_trade_dates_in_range", return_value=["20261001"]), \
                 patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
                 patch("src.data_platform.db.get_conn", MagicMock()):
                h = engine._make_tier1_handler(
                    "featured_daily", "top_list", "top_list", ["trade_date", "ts_code"])
                h({"id": "top_list_sync", "provider": "spy100",
                   "last_sync_date": "20260930"}, "20261002")
            assert _SPY_CALLS == [("featured_daily", "top_list", {"trade_date": "20261001"})], _SPY_CALLS
        finally:
            _ADAPTERS.pop("spy100", None)

    def test_full_rebuild_routes_to_declared_provider(self):
        from src.data_platform.adapters.base import _ADAPTERS, register_adapter
        from src.data_sync import engine
        _SPY_CALLS.clear()
        register_adapter(_make_spy_adapter_cls())
        try:
            with patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
                 patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
                 patch("src.data_platform.db.get_conn", MagicMock()):
                h = engine._make_full_rebuild_handler(
                    "static_list", "namechange", "namechange",
                    ["ts_code", "name", "start_date"], [], date_param=None)
                h({"id": "namechange_sync", "provider": "spy100"}, "20261002")
            assert _SPY_CALLS and _SPY_CALLS[0][:2] == ("static_list", "namechange"), _SPY_CALLS
            assert _SPY_CALLS[0][2] == {}, "快照表不该带窗口参数"
        finally:
            _ADAPTERS.pop("spy100", None)


# ---------------------------------------------------------------------------
# 4：源码级 —— 供给路径无 importlib 硬编码
# ---------------------------------------------------------------------------

class TestNoHardcodedTushare:
    def test_engine_source_has_no_importlib_tushare_adapter(self):
        from src.data_sync import engine
        src = inspect.getsource(engine)
        assert 'import_module("src.data_platform.adapters.tushare_adapter")' not in src, \
            "供给面分派仍硬编码 importlib 直连 tushare_adapter"

    def test_two_factories_use_supply_adapter(self):
        from src.data_sync import engine
        for fn in (engine._make_tier1_handler, engine._make_full_rebuild_handler):
            assert "_get_supply_adapter(" in inspect.getsource(fn), \
                f"{fn.__name__} 未走 _get_supply_adapter（provider 失效）"


# ---------------------------------------------------------------------------
# 5：真库对账 —— 工厂 (kind, sub_kind) == sync_kind_config 归置行
# ---------------------------------------------------------------------------

def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestRegistryReconcilesWithSyncKindConfig:
    def test_factory_keys_match_placement_rows(self):
        """工厂声明的 (kind, sub_kind) 与 `sync_kind_config` 归置行逐项一致（漂移即红）。"""
        from src.data_platform.db import get_conn
        from src.data_sync import engine
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sync_id, kind, sub_kind FROM sync_kind_config").fetchall()
        place = {r[0]: (r[1], r[2]) for r in rows}
        declared = {sid: (k, s) for sid, (k, s, _t, _p) in engine._TIER1_BATCH.items()}
        declared |= {sid: (k, s) for sid, (k, s, _t, _p, _d) in engine._TIER1_FULL.items()}
        for sid, ks in declared.items():
            assert sid in place, f"{sid} 无 sync_kind_config 归置行"
            assert place[sid] == ks, f"{sid} 声明 {ks} != 归置行 {place[sid]}"
