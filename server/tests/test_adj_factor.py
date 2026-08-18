"""复权因子（A/B-F1）：同步 join 正确性 + 降级容错（积分未到账不崩）+ 回填 SQL 形状。
契约：bar_1D = 未复权价 + 逐行 adj_factor（NULL=降级/无因子），2026-08-18。"""
from unittest.mock import patch, MagicMock
import pandas as pd


def _daily_df():
    return pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "20260815", "open": 9.0, "high": 9.1,
         "low": 8.9, "close": 9.05, "vol": 1000, "amount": 9050},
        {"ts_code": "000001.SZ", "trade_date": "20260815", "open": 10.0, "high": 10.2,
         "low": 9.9, "close": 10.1, "vol": 2000, "amount": 20200},
    ])


class TestDailyToRowsAdj:
    def test_factor_joined_into_row(self):
        from src.data_sync.engine import _daily_to_rows
        rows = _daily_to_rows(_daily_df(), adj_map={"600000.SH": 12.5})
        assert rows[0][9] == 12.5          # adj_factor 字段
        assert rows[1][9] is None          # 不在 map 的标保持 NULL
        assert rows[0][10] == "tushare"

    def test_adj_map_degraded_returns_empty_and_rows_still_built(self):
        """降级（因子接口不可用）→ adj_map={} → 行照常产出（因子 NULL），同步不中断。"""
        from src.data_sync.engine import _daily_to_rows, _adj_map_for_df
        with patch("src.data_platform.adapters.tushare_adapter.pull_adj_factor_by_date",
                   return_value=None):
            m = _adj_map_for_df(_daily_df())
        assert m == {}
        rows = _daily_to_rows(_daily_df(), m)
        assert len(rows) == 2 and all(r[9] is None for r in rows)

    def test_adj_map_joins_by_trade_date(self):
        from src.data_sync.engine import _adj_map_for_df
        fdf = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260815"],
                            "adj_factor": [3.3]})
        with patch("src.data_platform.adapters.tushare_adapter.pull_adj_factor_by_date",
                   return_value=fdf):
            m = _adj_map_for_df(_daily_df())
        assert m == {"600000.SH": 3.3}


class TestPullDailyRawDefault:
    def test_default_adj_is_none(self):
        """修复路径默认不复权（qfq 价曾混入 bar_1D 造成口径跳变，A-F1）。"""
        import inspect
        from src.data_platform.adapters.tushare_adapter import pull_daily
        assert inspect.signature(pull_daily).parameters["adj"].default is None


class TestBackfillAdjFactor:
    def test_degraded_returns_status_not_raise(self):
        """积分未到账：包装层返回 None（降级契约）→ backfill 返回 degraded，不抛异常。"""
        from src.data_sync.engine import backfill_adj_factor
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = [("2026-08-15",)]
        with patch("src.data_sync.engine.get_conn", return_value=mock_conn), \
             patch("src.data_platform.adapters.tushare_adapter.pull_adj_factor_by_date",
                   return_value=None), \
             patch("src.data_platform.adapters.tushare_adapter._adj_degraded_alert"), \
             patch("src.data_platform.adapters.tushare_adapter._adj_degraded", {"ts": 0.0}):
            r = backfill_adj_factor()
        assert r["status"] == "degraded"
        assert "积分" in r["reason"] or "接口" in r["reason"]

    def test_update_only_fills_null_rows_and_uses_range_predicate(self):
        from src.data_sync.engine import backfill_adj_factor
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = [("2026-08-15",)]
        fdf = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260815"],
                            "adj_factor": [7.7]})
        with patch("src.data_sync.engine.get_conn", return_value=mock_conn), \
             patch("src.data_platform.adapters.tushare_adapter.pull_adj_factor_by_date",
                   return_value=fdf), \
             patch("src.data_platform.adapters.tushare_adapter._adj_degraded", {"ts": 0.0}):
            r = backfill_adj_factor()
        assert r["status"] == "success"
        cur = mock_conn.cursor.return_value.__enter__.return_value
        sql = cur.executemany.call_args.args[0]
        assert "AND adj_factor IS NULL" in sql            # 不覆盖非空行
        assert "ts >= %s AND ts < %s" in sql              # F-F1：范围谓词（18x，ts::date 用不上索引）

    def test_dates_query_scoped_to_astock_symbols(self):
        """F-S1：只扫股票行（asset_static_info）——ETF 因子在 fund_adj、转债无，不限定则永不收敛。"""
        from src.data_sync.engine import backfill_adj_factor
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch("src.data_sync.engine.get_conn", return_value=mock_conn):
            backfill_adj_factor()
        dates_sql = mock_conn.execute.call_args.args[0]
        assert "asset_static_info" in dates_sql

    def test_wrapper_swallows_permission_exception(self):
        """包装层本身：Tushare 抛权限异常 → 返回 None 不上抛（同步绝不因此中断）。"""
        from src.data_platform.adapters.tushare_adapter import pull_adj_factor_by_date
        fake_pro = MagicMock()
        fake_pro.adj_factor.side_effect = Exception("抱歉，您没有访问该接口的权限")
        with patch("src.data_platform.adapters.tushare_adapter.get_pro", return_value=fake_pro), \
             patch("src.data_platform.adapters.tushare_adapter._adj_degraded_alert"), \
             patch("src.data_platform.adapters.tushare_adapter._adj_degraded", {"ts": 0.0}):
            assert pull_adj_factor_by_date("20260815") is None


class TestWiringRegressions:
    """E-1 教训：session_edge 曾在两个文件用了没导入（NameError 上线即崩，270 测试全绿也没抓到
    ——主循环无测试覆盖）。此测试锁 import 接线这类静态断点。"""

    def test_session_edge_imported_where_used(self):
        import re
        for path in ["src/md_hub/main.py", "src/strategy_runner/hub_worker.py"]:
            src = open(path).read()
            if "session_edge(" in src:
                assert re.search(r"^.*import.*\bsession_edge\b.*$", src, re.M), \
                    f"{path} 使用 session_edge 但未导入（上线即 NameError）"

    def test_daily_rows_adj_map_none_and_nan_kept_none(self):
        """F 核对 8：adj_map 值为 None/NaN 不得变 0.0（0 是合法因子值，毒化复权计算）。"""
        from src.data_sync.engine import _daily_to_rows
        rows = _daily_to_rows(_daily_df(), adj_map={"600000.SH": None, "000001.SZ": float("nan")})
        assert rows[0][9] is None and rows[1][9] is None
