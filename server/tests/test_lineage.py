"""批 62a：血缘三列测试钉（A03 §15.4 监管链——实盘注入/回测合并/帧级聚合）。"""
from unittest.mock import MagicMock, patch


class TestMergeRunLineage:
    def _conn(self, row):
        conn = MagicMock(); conn.__enter__.return_value = conn
        cur = MagicMock(); cur.fetchone.return_value = row
        conn.execute.return_value = cur
        return conn

    def test_merge_dedup_greatest_first_version(self):
        """source 去重并集/fetched_at GREATEST/dataset_version 首值保持（B-P1-1 语义钉）。"""
        from datetime import datetime, timezone as tz
        from src.scheduler.tasks import _merge_run_lineage
        from types import SimpleNamespace
        conn = self._conn(("tushare", None))   # 现 source=tushare，version 首算
        t1 = datetime(2026, 9, 27, 10, 0, tzinfo=tz.utc)
        frame = SimpleNamespace(source="xtp,tushare", fetched_at=t1, kind="bar_daily")
        # max(dataset_version) 查询：第二个 execute 返回
        conn.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=("tushare", None))),
            MagicMock(fetchone=MagicMock(return_value=(2,))),
            MagicMock(),
        ]
        with patch("src.data_platform.db.get_conn", return_value=conn):
            _merge_run_lineage(7, frame)
        upd = conn.execute.call_args_list[-1]
        sql, args = upd.args[0], upd.args[1]
        assert "UPDATE backtest_runs" in sql and "GREATEST" in sql
        assert args[0] == "tushare,xtp"      # 去重并集（排序 join）
        assert args[3] == "store:bar_daily:2"   # version=max(bar 表)，首值语义
        assert args[-1] == 7                    # run_id 末位

    def test_merge_keeps_existing_version_and_never_raises(self):
        """version 已在场=COALESCE 保持；DB 异常 never-raise（血缘不阻断回测）。"""
        from src.scheduler.tasks import _merge_run_lineage
        from types import SimpleNamespace
        frame = SimpleNamespace(source="xtp", fetched_at=None, kind="bar_daily")
        with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db down")):
            _merge_run_lineage(7, frame)   # 不抛即过

    def test_merge_existing_version_not_recomputed(self):
        from src.scheduler.tasks import _merge_run_lineage
        from types import SimpleNamespace
        conn = self._conn(("tushare", "store:bar_daily:1"))
        frame = SimpleNamespace(source="xtp", fetched_at=None, kind="bar_daily")
        conn.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=("tushare", "store:bar_daily:1"))),
            MagicMock(),
        ]
        with patch("src.data_platform.db.get_conn", return_value=conn):
            _merge_run_lineage(7, frame)
        args = conn.execute.call_args_list[-1].args[1]
        assert args[1] == "store:bar_daily:1"   # 首值保持（不重算）


class TestFrameSourceAggregate:
    def test_multi_source_join(self):
        """bar_1min 混源帧：distinct 多值逗号 join（B-P2-6 裁定）。"""
        import pandas as pd
        from src.data_platform.databus import DataBus
        from src.quant_common.contract import DataRequest, DataGap
        from datetime import datetime, timezone as tz
        ts = datetime(2026, 9, 18, tzinfo=tz.utc)   # to_contract 校验 ts 须 UTC aware
        df = pd.DataFrame([
            ("600000.SHSE", "1min", ts, 1, 1, 1, 1, 1, 1, 1.0, "tushare"),
            ("600000.SHSE", "1min", ts, 1, 1, 1, 1, 1, 1, 1.0, "xtp"),
        ], columns=["symbol", "freq", "ts", "open", "high", "low",
                    "close", "volume", "amount", "adj_factor", "source"])
        req = DataRequest(kind="bar_minute", symbols=("600000.SHSE",), temporality="historical",
                          freq="1min", range_=(ts, ts))
        with patch("src.data_platform.db.get_bars", return_value=df):
            frame = DataBus()._local_fetch(req)
        assert frame.source == "tushare,xtp"
