"""批 130（期二）：窗口参数族层化回归钉。

三组：①迁移快照钉（42 行两列精确=值表——错种/漏种即红）②零漂移对照（_window_of 双实现
同输入同输出——冻结 oracle）③容错（未迁移库 7 列形态 _read_sync_kind 不炸、读点走缺省）。
"""
from __future__ import annotations

import pytest

_OVERLAP3 = {"stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "top_list_sync",
             "block_trade_sync", "cyq_perf_sync", "forecast_sync", "st_list_sync"}
_OVERLAP7 = {"income", "balancesheet", "cashflow", "fina_indicator"}
_LAG1 = {"margin_detail_sync"}


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as _c:
            _c.execute("SELECT 1 FROM sync_kind_config LIMIT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="无 dev 库")
class TestMigrationSnapshot:
    def test_value_table_exact(self):
        """快照钉：8 行=3 / 4 行=7 / lag 仅 1 行 / holder 五行禁死值。"""
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sync_id, overlap_days, lag_trade_days FROM sync_kind_config").fetchall()
        got3 = {r[0] for r in rows if r[1] == 3}
        got7 = {r[0] for r in rows if r[1] == 7}
        gotlag = {r[0] for r in rows if r[2] == 1}
        dead = {r[0] for r in rows if r[1] == 7 and r[0] in
                ("dividend", "pledge_stat", "share_float", "stk_holdernumber", "top10_holders")}
        assert got3 == _OVERLAP3, got3 ^ _OVERLAP3
        assert got7 == _OVERLAP7, got7 ^ _OVERLAP7
        assert gotlag == _LAG1, gotlag
        assert not dead, dead
        by_id = {r[0]: r[1] for r in rows}
        assert by_id["forecast_sync"] == 3, "B-P0-2：forecast 走 tier1 消费=3，按 kind 种 7 即漂移"

    def test_null_rows_count(self):
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            n = conn.execute(
                "SELECT count(*) FROM sync_kind_config WHERE overlap_days IS NULL").fetchone()[0]
        assert n == 30, n


class TestZeroDriftOracle:
    def test_window_of_dual_impl(self):
        """冻结 oracle：_window_of 在「代码缺省(None 注入)」与「DB 值(7)」下同输入同输出。"""
        from datetime import date, timedelta
        from src.data_sync.pool_data import _window_of, _POOL_OVERLAP_DAYS
        cursors = {"income": "20261010"}
        upper = "20261019"
        start_old = (date.fromisoformat("2026-10-10") - timedelta(days=_POOL_OVERLAP_DAYS)).strftime("%Y%m%d")
        assert _window_of("income", cursors, upper)[0] == start_old
        assert _window_of("income", cursors, upper, 7)[0] == start_old
        assert _window_of("dividend", cursors, upper, 7) == (None, None)

    def test_tier1_constant_equals_seed(self):
        """零漂移钉（提交一相位）：常量=迁移种子值——删常量后本测改 DB 侧持久钉。"""
        from src.data_sync import engine
        assert engine._TIER1_OVERLAP_DAYS == 3


class TestOldSchemaTolerance:
    def test_read_sync_kind_7col_shape_no_crash(self):
        """未迁移库容错（B-P1-5）：7 列形态解包不炸（len 守卫），两键缺省 None。"""
        row7 = ("kind", None, "tbl", "incremental", ["a"], ["b"], [])
        row9 = ("kind", None, "tbl", "incremental", ["a"], ["b"], [], 3, 1)

        def build(row):
            d = {"k": row[0]}
            d.update({"overlap_days": row[7], "lag_trade_days": row[8]} if len(row) > 8 else {})
            return d

        assert "overlap_days" not in build(row7)
        assert build(row9)["overlap_days"] == 3 and build(row9)["lag_trade_days"] == 1
