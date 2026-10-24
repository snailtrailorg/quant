# -*- coding: utf-8 -*-
"""批 146 · 全量重建族幂等修——回归四钉。

①工厂 pk 去重（上游同键重复行不再撞 duplicate key）
②concept 窄投影（新版 12 列+重名列 → 仅表列）
③去重后 df 喂 _derive_st_states（namechange 链）
④C 族阈值分档（trade_cal=370 年度不误伤 / 其余 7 天照报）
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from src.data_sync import engine
from src.data_sync.pool_reconcile import check_family_c


def _ctx_of(conn):
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm


def _mk_adapter(df):
    a = MagicMock()
    a.fetch_supply.return_value = df
    return a


def _mk_conn_capture():
    conn = MagicMock()
    state = {"batches": [], "sqls": []}
    cur = MagicMock()

    def _exec(sql, *args):
        state["sqls"].append(sql)
        return MagicMock()

    conn.execute.side_effect = _exec
    cur.executemany.side_effect = lambda sql, batch: state["batches"].append((sql, batch))
    conn.cursor.return_value.__enter__.return_value = cur
    cm = _ctx_of(conn)
    return cm, state


class TestFactoryDedup:
    """钉①③：同键两行 → INSERT 去重后行数；派生链收去重后 df。"""

    def _handler(self):
        return engine._make_full_rebuild_handler(
            "static_list", "namechange", "namechange",
            ["ts_code", "name", "start_date"], date_param=None)

    def test_duplicate_rows_deduped_before_insert(self):
        df = pd.DataFrame([
            {"ts_code": "002122.SZ", "name": "汇洲智能", "start_date": "20230605"},
            {"ts_code": "002122.SZ", "name": "汇洲智能", "start_date": "20230605"},  # 上游重复
            {"ts_code": "688247.SH", "name": "宣泰医药", "start_date": "20220825"},
        ])
        cm, st = _mk_conn_capture()
        with patch.object(engine, "_get_supply_adapter", return_value=_mk_adapter(df)), \
             patch.object(engine, "_get_rate_ds", return_value="tushare"), \
             patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
             patch("src.data_platform.db.get_conn", return_value=cm), \
             patch.object(engine, "_derive_st_states") as ds:
            ret = self._handler()({"id": "namechange_sync", "provider": "tushare"}, "20261024")
        assert ret["saved"] == 2, ret                # 去重后 2 行
        sql, batch = st["batches"][0]
        assert len(batch) == 2
        ds.assert_called_once()                       # 钉③：派生收去重后 df
        assert len(ds.call_args.args[0]) == 2

    def test_pk_missing_in_df_skips_dedup(self):
        """交集空跳过去重（B-P2a）——不炸、不退化全列去重。"""
        df = pd.DataFrame([{"code": "A", "x": 1}, {"code": "A", "x": 2}])  # 无任何 pk 列
        cm, st = _mk_conn_capture()
        with patch.object(engine, "_get_supply_adapter", return_value=_mk_adapter(df)), \
             patch.object(engine, "_get_rate_ds", return_value="tushare"), \
             patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
             patch("src.data_platform.db.get_conn", return_value=cm), \
             patch.object(engine, "_derive_st_states"):
            ret = self._handler()({"id": "namechange_sync", "provider": "tushare"}, "20261024")
        assert ret["saved"] == 2                       # 未去重（交集空）

    def test_empty_df_fail_loud(self):
        cm, st = _mk_conn_capture()
        with patch.object(engine, "_get_supply_adapter", return_value=_mk_adapter(pd.DataFrame())), \
             patch.object(engine, "_get_rate_ds", return_value="tushare"), \
             patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()):
            ret = self._handler()({"id": "namechange_sync", "provider": "tushare"}, "20261024")
        assert ret["failed_dates"] == ["空数据"]


class TestConceptProjection:
    """钉②：新版 12 列（含重名列）→ 窄投影仅 ts_code/name。"""

    def test_project_12col_with_dupname(self):
        # 真重名列须 concat 构造（字典构造 pandas 即折叠去重——码审 B P1-146a）
        base = pd.DataFrame({
            "ts_code": ["A", "B"],
            "name": ["概念A", "概念B"],
            "code": ["1", "2"],
            "class1_code": ["c1", "c1"],
            "trade_date": ["20261024"] * 2,
            "item_size": [10, 20],
        })
        extra = pd.DataFrame({
            "name": ["概念A2", "概念B2"],        # 第二个 name 列（上游 12 列重名形态）
            "class1_name": ["行业", "行业"],
        })
        raw = pd.concat([base, extra], axis=1)
        with patch("src.data_platform.adapters.tushare_adapter.get_pro") as gp:
            gp.return_value.concept.return_value = raw
            from src.data_platform.adapters.tushare_adapter import pull_concept
            out = pull_concept()
        assert list(out.columns) == ["ts_code", "name"], out.columns
        assert out.shape[1] == 2, out.shape          # 重名变形下不多列漏网

    def test_passthrough_when_upstream_downlevel(self):
        """上游若回旧版两列——投影零漂移。"""
        df = pd.DataFrame({"ts_code": ["A"], "name": ["X"]})
        with patch("src.data_platform.adapters.tushare_adapter.get_pro") as gp:
            gp.return_value.concept.return_value = df
            from src.data_platform.adapters.tushare_adapter import pull_concept
            out = pull_concept()
        assert list(out.columns) == ["ts_code", "name"]


class TestCFamilyThresholds:
    """钉④：trade_cal 200 天不报；astock_list 200 天报。"""

    def _rows(self, ts):
        return [("trade_cal", "idle", ts), ("astock_list", "idle", ts)]

    def test_split_thresholds(self):
        ts = datetime.now(timezone.utc) - timedelta(days=200)
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = self._rows(ts)
        conn.execute.return_value = cur
        cm2 = _ctx_of(conn)
        from src.data_sync import pool_reconcile as pr
        with patch.object(pr, "get_conn", return_value=cm2):
            findings = check_family_c()
        assert not any("交易日历" in f for f in findings), findings
        assert any("A股列表" in f for f in findings), findings
