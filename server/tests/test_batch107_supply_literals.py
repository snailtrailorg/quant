"""批 107 · 批100 增量二（7 字面量供给项经 adapter）+ 103b A 步（首个「非 bar 多源」样板）。

验收五点：
1. **注册表完备**：`_LITERAL_SUPPLY` 7 项 ↦ `_SUPPLY_PULL` 键齐；↦ `sync_kind_config` 归置行对账（真库）。
2. **消费者断言**：替身 provider ⇒ 7 个 handler 都改走它（`fetch_supply` 被调且 `(kind, sub_kind)` 正确）。
3. **聚宽样板**：`fetch_supply("static_list","stock")` 归一化到 `asset_static_info` 列形状；能力声明生效。
4. **列形状单一真源**：engine `_TIER1_*` 声明列 == `sync_kind_config` 列（漂移即红）；
   `sync_config.pg_table == sync_kind_config.pg_table`（`etf_list` 错配修复的守卫）。
5. **反证（源码级）**：7 handler 均含 `_get_supply_adapter(` 且**无** `_get_pro(` 直连。
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


_SPY_CALLS: list = []


def _spy_df(kind, sub_kind):
    """按 (kind, sub_kind) 返回形状合理的小 df（供 handler 的 rows 构造消费）。"""
    if kind == "trade_cal":
        return pd.DataFrame({"exchange": ["SSE"], "cal_date": ["20260101"],
                             "is_open": [1], "pretrade_date": ["20251231"]})
    if kind == "fundamental_daily":
        return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260930"],
                             "close": [10.0], "pe": [8.0]})
    if kind == "static_list" and sub_kind in ("convertible", "terms"):
        return pd.DataFrame({"ts_code": ["110000.SH"], "bond_short_name": ["测试转债"],
                             "conv_price": [12.3]})
    if kind == "static_list" and sub_kind == "etf":
        return pd.DataFrame({"ts_code": ["510300.SH"], "name": ["沪深300ETF"],
                             "management": ["华泰"], "fund_type": ["股票型"],
                             "invest_type": ["被动"], "list_date": ["20120528"]})
    # stock / symbols
    return pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"],
                         "industry": ["银行"], "market": ["主板"],
                         "list_status": ["L"], "list_date": ["19910403"], "delist_date": [""]})


def _make_spy_cls():
    from src.data_platform.adapters.base import BaseDataAdapter

    class _Cls(BaseDataAdapter):
        provider = "spy107"

        def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
            return pd.DataFrame()

        def pull_minute(self, symbol, freq, start, end):
            return pd.DataFrame()

        def to_bar_rows(self, df, freq, adj_map=None):
            return []

        def available_range(self, kind):     # 批 108·步 3：源界（窗口第四边界硬契约）
            return ("2010-01-01", None)

        def publish_lag(self, kind):
            return 0

        def fetch_supply(self, kind, sub_kind=None, **params):
            _SPY_CALLS.append((kind, sub_kind, params))
            return _spy_df(kind, sub_kind)

    return _Cls


# ---------------------------------------------------------------------------
# 1：注册表完备
# ---------------------------------------------------------------------------

class TestLiteralRegistry:
    _SEVEN = ("astock_basic", "astock_list", "static_symbols", "cb_basic",
              "convertible_terms", "etf_list", "trade_cal")

    def test_seven_items_declared(self):
        from src.data_sync import engine
        assert set(engine._LITERAL_SUPPLY) == set(self._SEVEN)

    def test_supply_pull_covers_all_literals(self):
        from src.data_platform.adapters.base import _SUPPLY_PULL
        from src.data_sync import engine
        for sid, (k, s, _t) in engine._LITERAL_SUPPLY.items():
            assert (k, s) in _SUPPLY_PULL, f"{sid} 的 ({k},{s}) 无 _SUPPLY_PULL 分派"

    def test_all_literal_pull_functions_exist(self):
        from src.data_platform.adapters import tushare_adapter as ta
        from src.data_platform.adapters.base import _SUPPLY_PULL
        from src.data_sync import engine
        for _sid, (k, s, _t) in engine._LITERAL_SUPPLY.items():
            fn = _SUPPLY_PULL[(k, s)]
            assert callable(getattr(ta, fn, None)), f"({k},{s}) → {fn} 不存在"

    def test_no_duplicate_kind_subkind_with_factories(self):
        """字面量项的 (kind, sub_kind) 不与工厂项撞键（撞键=两义）。"""
        from src.data_sync import engine
        factory = {(k, s) for (k, s, _t, _p) in engine._TIER1_BATCH.values()}
        factory |= {(k, s) for (k, s, _t, _p, _d) in engine._TIER1_FULL.values()}
        lit = {(k, s) for (k, s, _t) in engine._LITERAL_SUPPLY.values()}
        assert not (factory & lit), f"字面量项与工厂项撞 (kind,sub_kind): {factory & lit}"


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestReconcileWithDb:
    def test_literal_keys_match_placement_rows(self):
        """`_LITERAL_SUPPLY` 的 (kind, sub_kind, pg_table) == `sync_kind_config` 归置行。"""
        from src.data_platform.db import get_conn
        from src.data_sync import engine
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sync_id, kind, sub_kind, pg_table FROM sync_kind_config").fetchall()
        place = {r[0]: (r[1], r[2], r[3]) for r in rows}
        for sid, (k, s, t) in engine._LITERAL_SUPPLY.items():
            assert sid in place, f"{sid} 无 sync_kind_config 归置行"
            assert place[sid] == (k, s, t), f"{sid} 声明 {(k, s, t)} != 归置行 {place[sid]}"

    def test_pg_table_single_source(self):
        """`sync_config.pg_table` == `sync_kind_config.pg_table`（批 107 修 `etf_list` 错配的守卫）。

        错配危害不止台账：`DELETE /api/sync/data/{sid}` 直接 `DELETE FROM "{pg_table}"`。
        """
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            bad = conn.execute(
                "SELECT c.id, c.pg_table, k.pg_table FROM sync_config c "
                "JOIN sync_kind_config k ON k.sync_id=c.id "
                "WHERE lower(c.pg_table) <> lower(k.pg_table)").fetchall()
        assert not bad, f"pg_table 双真源错配: {bad}"

    def test_factory_declared_cols_match_placement_rows(self):
        """engine `_TIER1_*` 声明列 == `sync_kind_config` 列（**批 107 列形状对账门**）。

        背景：`_TIER1_BATCH`(pk) + `_TIER1_FLOAT_COLS`/`_TIER1_TEXT_COLS` 与 `sync_kind_config`
        的 `pk_cols/float_cols/text_cols` 是同一份列形状的两处声明（内联 dict 保留作 import 骨架
        ——engine import 时不得触库）。本门把两处钉成一致：漂移即红。
        """
        from src.data_platform.db import get_conn
        from src.data_sync import engine
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sync_id, pk_cols, float_cols, text_cols FROM sync_kind_config").fetchall()
        place = {r[0]: (set(r[1] or []), set(r[2] or []), set(r[3] or [])) for r in rows}
        declared = {}
        for sid, (_k, _s, tbl, pk) in engine._TIER1_BATCH.items():
            declared[sid] = (set(pk), set(engine._TIER1_FLOAT_COLS.get(tbl, [])),
                             set(engine._TIER1_TEXT_COLS.get(tbl, [])))
        for sid, (_k, _s, tbl, pk, _d) in engine._TIER1_FULL.items():
            declared[sid] = (set(pk), set(engine._TIER1_FLOAT_COLS.get(tbl, [])),
                             set(engine._TIER1_TEXT_COLS.get(tbl, [])))
        for sid, (pk, fl, tx) in declared.items():
            assert sid in place, f"{sid} 无归置行"
            assert place[sid] == (pk, fl, tx), f"{sid} 列形状漂移：声明 {(pk, fl, tx)} != 库 {place[sid]}"


# ---------------------------------------------------------------------------
# 2：消费者断言 —— provider 真生效（核心）
# ---------------------------------------------------------------------------

class TestLiteralProviderEffective:
    """`sync_config.provider` 对 7 字面量项真生效（批 107 核心目标）。"""

    def _run(self, sid, handler):
        from src.data_platform.adapters.base import _ADAPTERS, register_adapter
        from src.data_sync import engine
        _SPY_CALLS.clear()
        register_adapter(_make_spy_cls())
        spy = _ADAPTERS["spy107"]()
        try:
            with patch.object(engine, "_get_supply_adapter", return_value=spy), \
                 patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
                 patch.object(engine, "get_conn", MagicMock()), \
                 patch.object(engine, "_trade_dates_in_range", return_value=["20260930"]), \
                 patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
                 patch("src.data_platform.adapters.tushare_adapter.save_daily_basic",
                       MagicMock(return_value=0)):
                handler({"id": sid, "provider": "spy107", "last_sync_date": "20260929"}, "20260930")
        finally:
            _ADAPTERS.pop("spy107", None)
        return list(_SPY_CALLS)

    def test_seven_handlers_route_to_provider(self):
        from src.data_sync import engine
        cases = {
            "astock_basic": engine._sync_astock_basic,
            "astock_list": engine._sync_astock_list,
            "static_symbols": engine._sync_static_list,
            "cb_basic": engine._sync_cb_basic,
            "convertible_terms": engine._sync_convertible_terms,
            "etf_list": engine._sync_etf_list,
            "trade_cal": engine._sync_trade_cal,
        }
        for sid, fn in cases.items():
            calls = self._run(sid, fn)
            assert calls, f"{sid} 未调 fetch_supply（provider 未生效）"
            expect = engine._LITERAL_SUPPLY[sid][:2]
            assert calls[0][:2] == expect, f"{sid} 分派键错：{calls[0][:2]} != {expect}"


# ---------------------------------------------------------------------------
# 3：聚宽样板
# ---------------------------------------------------------------------------

class TestJoinQuantSupplySample:
    def test_capability_declared(self):
        from src.data_platform.capabilities import provider_capabilities, provider_domain
        caps = provider_capabilities("joinquant")
        assert "ref_data" in caps, f"joinquant 缺 ref_data: {caps}"
        assert "hist_quote" in caps
        assert provider_domain("joinquant") == "data_source"

    def test_static_list_stock_normalized_shape(self):
        """`fetch_supply("static_list","stock")` 输出列 == `asset_static_info` 列形状。"""
        from src.data_platform.adapters.base import get_adapter
        ad = get_adapter("joinquant")

        class _FakeJQ:
            @staticmethod
            def get_all_securities(types=None, date=None):
                idx = ["000001.XSHE", "300750.XSHE", "688981.XSHG", "600000.XSHG"]
                return pd.DataFrame(
                    {"display_name": ["平安银行", "宁德时代", "中芯国际", "浦发银行"],
                     "name": ["PAYH", "NDSD", "ZXGJ", "PFYH"],
                     "start_date": ["1991-04-03", "2018-06-11", "2020-07-16", "1999-11-10"],
                     "end_date": ["2200-01-01"] * 4,
                     "type": ["stock"] * 4}, index=idx)

        with patch.object(ad, "get_client", return_value=_FakeJQ()):
            df = ad.fetch_supply("static_list", "stock")
        assert list(df.columns) == ["ts_code", "name", "industry", "market",
                                    "list_status", "list_date", "delist_date"], list(df.columns)
        assert df["ts_code"].tolist() == ["000001.SZ", "300750.SZ", "688981.SH", "600000.SH"]
        assert df["list_status"].tolist() == ["L"] * 4
        assert df["list_date"].tolist() == ["19910403", "20180611", "20200716", "19991110"]
        assert df["delist_date"].tolist() == [""] * 4           # 哨兵 2200-01-01 → 空串
        assert df["market"].tolist() == ["主板", "创业板", "科创板", "主板"]

    def test_unsupported_supply_raises(self):
        from src.data_platform.adapters.base import UnsupportedFeature, get_adapter
        ad = get_adapter("joinquant")
        with pytest.raises(UnsupportedFeature):
            ad.fetch_supply("static_list", "etf")      # 聚宽未实现 ETF 清单


# ---------------------------------------------------------------------------
# 5：反证（源码级）
# ---------------------------------------------------------------------------

class TestNoDirectProInLiterals:
    _FNS = ("_sync_astock_basic", "_sync_astock_list", "_sync_static_list", "_sync_cb_basic",
            "_sync_convertible_terms", "_sync_etf_list", "_sync_trade_cal")

    def test_literals_use_supply_adapter(self):
        from src.data_sync import engine
        for name in self._FNS:
            src = inspect.getsource(getattr(engine, name))
            assert "_get_supply_adapter(" in src, f"{name} 未走 _get_supply_adapter"
            assert "_get_pro(" not in src, f"{name} 仍有 _get_pro 直连（provider 会失效）"


# ---------------------------------------------------------------------------
# 6：迁移 0138 结构（source 级；不打库）
# ---------------------------------------------------------------------------

class TestMigration0138Shape:
    def _mod(self):
        import importlib
        return importlib.import_module("migrations.versions.0138_supply_literals_kind_rows")

    def test_revision_chain(self):
        m = self._mod()
        assert m.revision == "0138" and m.down_revision == "0137"

    def test_expand_only_no_ddl(self):
        """expand-only：upgrade 零 DDL（无 ALTER/DROP/TRUNCATE/RENAME）。"""
        src = inspect.getsource(self._mod().upgrade).upper()
        for bad in ("ALTER TABLE", "DROP ", "TRUNCATE", "RENAME"):
            assert bad not in src, f"upgrade 含 DDL 语句 {bad}"

    def test_adds_two_placement_rows(self):
        """补 `static_symbols`/`convertible_terms` 归置行——`(kind, sub_kind)` 是 fetch_supply 分派键。"""
        src = inspect.getsource(self._mod().upgrade)
        assert "'static_symbols', 'static_list', 'symbols', 'static_symbols'" in src, (
            "缺 static_symbols 归置行 ⇒ 该 sync_id 的 fetch_supply 无键可依")
        assert "'convertible_terms', 'static_list', 'terms', 'convertible_terms'" in src, (
            "缺 convertible_terms 归置行")
        assert "ON CONFLICT (sync_id) DO NOTHING" in src, "须幂等——复跑不得覆盖运维编辑"

    def test_fixes_etf_list_pg_table_mismatch(self):
        """`etf_list.pg_table` 由 `asset_static_info`（错）改为 `etf_basic_info`。

        危害：`DELETE FROM "{pg_table}"` 会删掉 A 股整表 ⇒ 跨项数据丢失。
        """
        src = inspect.getsource(self._mod().upgrade)
        assert "UPDATE sync_config SET pg_table='etf_basic_info'" in src
        assert "pg_table='asset_static_info'" in src, "须带旧值守卫（只改错配行）"

    def test_downgrade_symmetric(self):
        src = inspect.getsource(self._mod().downgrade)
        assert "DELETE FROM sync_kind_config WHERE sync_id='static_symbols'" in src
        assert "DELETE FROM sync_kind_config WHERE sync_id='convertible_terms'" in src
        assert "pg_table='asset_static_info'" in src

    def test_uses_op_execute_not_exec_driver_sql(self):
        """离线渲染（`alembic upgrade --sql`）下 `exec_driver_sql` 会炸（同 0132/0133 教训）。"""
        m = self._mod()
        for fn in (m.upgrade, m.downgrade):
            src = inspect.getsource(fn)
            assert "exec_driver_sql" not in src, "纯数据迁移须用 op.execute（离线渲染安全）"
            assert "op.execute(" in src, "须走 op.execute"
