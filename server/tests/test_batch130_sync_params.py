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
        """未迁移库容错（B-P1-5）：mock get_conn 返回 7 元组真调 _read_sync_kind——
        不炸、两键缺省缺失（盲审 B-P1-a①：禁自证 build 复刻，必须走真函数）。"""
        from unittest.mock import patch, MagicMock
        from src.data_sync import engine

        class _Cur:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        class _Conn:
            def __init__(self, cur):
                self._cur = cur

            def execute(self, *a, **k):
                return self._cur

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        row7 = ("kind", None, "tbl", "incremental", ["a"], ["b"], [])
        with patch.object(engine, "get_conn", return_value=_Conn(_Cur(row7))):
            d = engine._read_sync_kind("x")
        assert "overlap_days" not in d and d["kind"] == "kind"

        row9 = ("kind", None, "tbl", "incremental", ["a"], ["b"], [], 3, 1)
        with patch.object(engine, "get_conn", return_value=_Conn(_Cur(row9))):
            d = engine._read_sync_kind("x")
        assert d["overlap_days"] == 3 and d["lag_trade_days"] == 1


class TestDbValueWiring:
    def test_tier1_handler_consumes_db_overlap_lag(self):
        """接线行为钉（盲审 B-P1-c：batch92 _build 不传 sync_id ⇒ DB 读点行为级零覆盖——
        本例带 sync_id 构造 handler，dev 真库真值（margin 行 overlap=3/lag=1），
        mock adapter/落库，断言窗口真消费 DB 值）。"""
        from unittest.mock import patch
        import pandas as pd
        from src.data_sync import engine

        handler = engine._make_tier1_handler(
            "featured_daily", "margin_detail", "margin_detail",
            ["trade_date", "ts_code"], float_cols=["o"],
            sync_id="margin_detail_sync", cols_from_sync_kind=True)

        class _FakeAdapter:
            venue = "XTP"

            def available_range(self, kind):
                return ("20100101", None)

            def publish_lag(self, kind):
                return 1

            def list_symbols(self, refresh=False):
                return ["X"]

            def symbol_inception(self, sym):
                return None

            def fetch_supply(self, kind, sub_kind=None, **p):
                return pd.DataFrame(
                    [{"ts_code": "X", "trade_date": p.get("trade_date", "20261019"), "o": 1.0}])

        cfg = {"id": "margin_detail_sync", "provider": "tushare", "last_sync_date": "20261018"}
        with patch.object(engine, "_get_supply_adapter", return_value=_FakeAdapter()), \
             patch.object(engine, "_get_rate_ds"), \
             patch.object(engine, "_data_ready_end_date", return_value="20261019") as _dre, \
             patch("src.data_platform.db.get_conn") as _gcmock:
            # 落库连接 mock（_gc 是函数内 import——patch db 层 get_conn 经 save 路径；
            # execute 返回裸 cursor 形态足够 upsert 路径零炸）
            _gcmock.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (1,)
            r = handler(cfg, "20261020", None)
        # lag=1（DB）⇒ _data_ready_end_date 收到 1 而非注册缺省 0
        assert _dre.call_args[0][0] == 1, _dre.call_args
        # overlap=3（DB）⇒ 下界=last+1-3=20261016
        assert r.get("start") == "20261016", r

    def test_pool_injects_db_overlap(self):
        """pool 注入链行为钉（复审 A2-GAP：engine 有接线钉而 pool 无——丢注入无测试会红）。
        patch _read_sync_kind 返 overlap=6 + spy _window_of：run 后断言 spy 收到 6 非 None。"""
        from unittest.mock import patch, MagicMock
        import pandas as pd
        from src.data_sync import pool_data as PD

        real_window = PD._window_of
        seen: list = []

        def _spy(table, cursors, upper, overlap_days=None):
            if table in PD._INCREMENTAL_TABLES:
                seen.append(overlap_days)
            return real_window(table, cursors, upper, overlap_days)

        fake_adapter = MagicMock()
        fake_adapter.fetch.return_value = pd.DataFrame({"ts_code": ["000001.SZ"], "ann_date": ["20261019"], "o": [1.0]})

        kc = {"kind": "financial_stmt", "sub_kind": "income", "pg_table": "income",
              "rebuild": "incremental", "pk_cols": ["ts_code", "ann_date"],
              "float_cols": ["o"], "text_cols": [], "overlap_days": 6, "lag_trade_days": None}

        with patch.object(PD, "_get_pool_ts_codes", return_value=["000001.SZ"]), \
             patch.object(PD, "_read_sync_kind", return_value=kc), \
             patch.object(PD, "_get_kline_adapter", return_value=fake_adapter), \
             patch.object(PD, "_get_rate_ds"), \
             patch.object(PD, "_load_cursors", return_value={"income": "20261010"}), \
             patch.object(PD, "_window_of", side_effect=_spy), \
             patch.object(PD, "_sink_frame", return_value=1):
            PD.run_pool_sync(cfg={"id": "pool_data", "provider": "tushare"},
                             symbols=["000001.SZ"], timebox_s=30)
        assert 6 in seen and None not in seen, seen   # DB 值真注入（丢注入=seen 全 None 即红）
