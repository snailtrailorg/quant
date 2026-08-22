"""项 18 三档新表新鲜度检测测试。

测试目标：_check_tier_freshness() 函数 -- 一档 9 表 sync_log 新鲜度、二档 4 增量表游标
+ pool_data 任务心跳（盲审 A-2：6 非增量表无游标由心跳覆盖）、异常容错。
纯 mock 不连真实 PG/Valkey。
"""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

import pytest


# ── 辅助 ──

def _fresh_ts(days_ago=0):
    """返回 days_ago 天前的 datetime。"""
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _fresh_tier1_rows():
    return [(sid, _fresh_ts(0.04)) for sid in [
        "stk_limit_sync", "moneyflow_sync", "margin_detail_sync",
        "top_list_sync", "block_trade_sync", "cyq_perf_sync",
        "forecast_sync", "namechange_sync", "concept_sync",
    ]]


def _fresh_cursor_rows():
    return [(tbl, _fresh_ts(1).strftime("%Y%m%d")) for tbl in
            ("income", "balancesheet", "cashflow", "fina_indicator")]


# ── 测试 ──

class TestTierFreshness:

    def test_fresh_tier1_no_alert(self):
        """一档 9 表 + 二档游标 + pool_data 心跳全新鲜 -> 返回空列表。"""
        from src.scheduler.tasks import _check_tier_freshness
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.side_effect = [
            _fresh_tier1_rows(),
            _fresh_cursor_rows(),
        ]
        conn.execute.return_value.fetchone.return_value = (_fresh_ts(0.04),)
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            result = _check_tier_freshness()
        assert result == []

    def test_stale_tier1_alert(self):
        """一档 1 表 sync 在 3 天前（超 48h）-> 返回该表条目。"""
        from src.scheduler.tasks import _check_tier_freshness
        stale_ts = _fresh_ts(3)  # 3 天前
        conn = MagicMock()
        conn.__enter__.return_value = conn
        tier1_rows = [r for r in _fresh_tier1_rows() if r[0] != "stk_limit_sync"]
        tier1_rows.append(("stk_limit_sync", stale_ts))
        conn.execute.return_value.fetchall.side_effect = [
            tier1_rows,
            _fresh_cursor_rows(),
        ]
        conn.execute.return_value.fetchone.return_value = (_fresh_ts(0.04),)
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            result = _check_tier_freshness()
        assert len(result) == 1
        assert result[0]["sync_id"] == "stk_limit_sync"
        assert result[0]["kind"] == "tier1"
        assert result[0]["age_hours"] is not None and result[0]["age_hours"] > 48

    def test_tier2_cursor_frozen(self):
        """二档游标表空 + pool_data 无 done 行（从未同步）-> 返回 4 表 + 心跳条目。"""
        from src.scheduler.tasks import _check_tier_freshness
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.side_effect = [
            _fresh_tier1_rows(),
            [],  # pool_data_cursor 空
        ]
        conn.execute.return_value.fetchone.return_value = None
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            result = _check_tier_freshness()
        # 二档 4 增量表从未同步 + pool_data 心跳缺失 = 5 条
        tier2 = [r for r in result if r["kind"] == "tier2"]
        assert len(tier2) == 5
        for r in tier2:
            assert r["last_ts"] is None
            assert r["age_hours"] is None

    def test_db_exception_returns_empty(self):
        """get_conn 抛异常 -> 返回空列表，不抛。"""
        from src.scheduler.tasks import _check_tier_freshness
        import src.data_platform.db as db
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            result = _check_tier_freshness()
        assert result == []