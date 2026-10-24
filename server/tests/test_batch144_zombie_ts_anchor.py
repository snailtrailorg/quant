# -*- coding: utf-8 -*-
"""批 144 · 僵尸复位误报根治——回归三钉。

根因（prod 10-22/10-23 连续两晨实证）：_mark_running(True) 只写 last_status 不刷
last_sync_ts，而调度器僵尸判定按 ts 龄 >7200s——async 长任务（binance 晨档 1069s）
跨调度轮即被误判「僵尸 running 已复位」。修法=置位同刷 ts。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.data_sync import engine
from src.scheduler import tasks as sched

TZ_CN = timezone(timedelta(hours=8))


def _conn_with_rows(rows):
    """get_conn 上下文：首次 execute 返回 configs 行，后续 UPDATE 捕获。"""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    state = {"execs": []}

    def _exec(sql, *a):
        state["execs"].append((sql, a))
        if "FROM sync_config WHERE enabled" in sql:
            return cur
        return MagicMock()

    conn.execute.side_effect = _exec
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm, state


def _ctx(conn):
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm


class TestMarkRunningSql:
    """钉①：SQL 文本——running 分支含 last_sync_ts=now()；idle 分支不含。"""

    def test_running_branch_refreshes_ts(self):
        conn = MagicMock()
        calls = []
        conn.execute.side_effect = lambda sql, *a: calls.append(sql)
        with patch.object(engine, "get_conn", return_value=_ctx(conn)):
            engine._mark_running("binance_perp_daily", True)
        assert "last_sync_ts=now()" in calls[0]

    def test_idle_branch_keeps_legacy_sql(self):
        conn = MagicMock()
        calls = []
        conn.execute.side_effect = lambda sql, *a: calls.append(sql)
        with patch.object(engine, "get_conn", return_value=_ctx(conn)):
            engine._mark_running("binance_perp_daily", False)
        assert "last_sync_ts" not in calls[0]


class TestZombieVerdict:
    """钉②③：真僵尸（running+ts=23h 前）复位 idle；运行中（running+ts=10min）仅跳过。"""

    def _run(self, ts_age_sec: int):
        ts = datetime.now(TZ_CN) - timedelta(seconds=ts_age_sec)
        rows = [("binance_perp_daily", "42 8 * * *", True, "running", "20261023", ts, "none")]
        cm, state = _conn_with_rows(rows)
        from src.data_platform import db as pdb
        import src.data_sync as ds_pkg
        with patch.object(pdb, "get_conn", return_value=cm), \
             patch.object(pdb, "is_trading_day", return_value=True), \
             patch.object(ds_pkg, "sync") as ms:   # 包级（调度器 from src.data_sync import sync 每次重解析）
            ret = sched.data_sync_scheduler()
        return ret, state["execs"], ms

    def test_true_zombie_reset_to_idle(self):
        ret, execs, ms = self._run(23 * 3600)
        resets = [e for e in execs if "last_status='idle'" in e[0]]
        assert resets, f"僵尸未复位: {execs}"
        assert any("binance_perp_daily" in str(e[1]) for e in resets)
        ms.assert_not_called()   # 复位分支 continue，本轮不触发

    def test_running_fresh_skipped_not_reset(self):
        ret, execs, ms = self._run(600)
        resets = [e for e in execs if "last_status='idle'" in e[0]]
        assert not resets, f"运行中被误复位: {execs}"
        assert any("运行中" in s for s in ret["skipped"])
        ms.assert_not_called()

    def test_long_running_1069s_not_reset(self):
        """码审 B P2-144a：事故峰区间（>1069s 且 <7200s）不复位——binance 实测运行时长档。"""
        ret, execs, ms = self._run(1200)
        resets = [e for e in execs if "last_status='idle'" in e[0]]
        assert not resets, f"长任务运行中被误复位: {execs}"
        assert any("运行中" in s for s in ret["skipped"])
