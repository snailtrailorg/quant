"""F2 根因收尾测试（G 审 6 场景）：游标三态 + 连续成功末日 + 空 df 成功 + 顺序锁死 + overwrite 校验。

背景：a28a5fa 起 "1D" 断言炸 → 逐日异常吞进 failed_dates → 游标照推 end_date → 日线静默断 11 天。
修复语义：返回 last_success_date 键的 handler 才走三态（全失败不动/部分失败推连续末日/全成功推末）；
其余 handler 无条件推进（分钟线 per-symbol 失败粒度不能被卷入三态——200 积分全失败会冻游标重试风暴）。
"""
from unittest.mock import patch, MagicMock
import pandas as pd

import pytest


def _df(day: str) -> pd.DataFrame:
    return pd.DataFrame([{"ts_code": "600000.SH", "trade_date": day, "open": 9.0,
                          "high": 9.1, "low": 8.9, "close": 9.05, "vol": 100, "amount": 905}])


class _FakeLock:
    acquired = True

    def __init__(self, sid):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run_sync(handler_ret, cfg_last="20260810"):
    """跑 engine.sync()，捕获终态写入与调用顺序。返回 (result, update_calls, running_calls, alerts)。"""
    from src.data_sync import engine
    fake_cfg = {"id": "astock_daily", "name": "x", "mode": "incremental", "enabled": True,
                "last_sync_date": cfg_last, "last_sync_ts": None, "last_status": "idle"}
    update_calls, running_calls, alerts = [], [], []

    handler = MagicMock(return_value=handler_ret)
    with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
         patch.object(engine, "_get_config", return_value=fake_cfg), \
         patch.object(engine, "_HANDLERS", {"astock_daily": handler}), \
         patch.object(engine, "_log"), \
         patch.object(engine, "_expected_trading_days", return_value=1), \
         patch.object(engine, "_mark_running",
                      side_effect=lambda sid, running: running_calls.append(running)), \
         patch.object(engine, "_update_sync_state",
                      side_effect=lambda *a, **k: update_calls.append((a, k))), \
         patch.object(engine, "_alert_sync_failure",
                      side_effect=lambda *a, **k: alerts.append(a)):
        result = engine.sync("astock_daily")
    return result, update_calls, running_calls, alerts


class TestCursorThreeState:
    def test_partial_failure_advances_to_last_consecutive_success(self):
        """中间失败后段成功：游标停**连续成功末日**（不是最大成功日——否则中间失败日永久漏）。"""
        ret = {"pulled": 10, "saved": 10, "start": "20260811", "failed_dates": ["20260812:Error:x"],
               "last_success_date": "20260811"}
        result, updates, _, alerts = _run_sync(ret)
        assert result["status"] == "partial"
        assert updates[-1][0] == ("astock_daily", "20260811", 10, "partial")
        assert alerts, "部分失败应主动告警"

    def test_all_failure_keeps_cursor_and_marks_failed(self):
        """全失败：游标不动（旧值）、status=failed、仍刷新 last_sync_ts（同一 UPDATE，G-S3）。"""
        ret = {"pulled": 0, "saved": 0, "start": "20260811",
               "failed_dates": ["20260811:Error:x", "20260812:Error:x"],
               "last_success_date": None}
        result, updates, _, _ = _run_sync(ret, cfg_last="20260810")
        assert result["status"] == "partial"
        assert updates[-1][0] == ("astock_daily", "20260810", 0, "failed")   # 游标=旧值

    def test_all_success_advances_to_end(self):
        ret = {"pulled": 10, "saved": 10, "start": "20260811", "failed_dates": [],
               "last_success_date": "20260818"}
        _, updates, _, alerts = _run_sync(ret)
        assert updates[-1][0][3] == "idle"
        assert not alerts

    def test_handler_without_key_advances_unconditionally(self):
        """无 last_success_date 键的 handler（分钟线 per-symbol 失败粒度）：有失败也照推——
        防重试风暴（G-S1）。"""
        ret = {"pulled": 0, "saved": 0, "start": "20260811",
               "failed_dates": ["000001.SZ:Error:积分不足"]}   # 分钟线失败粒度=per-symbol
        _, updates, _, _ = _run_sync(ret)
        assert updates[-1][0][3] == "idle"   # 推进且 idle（现状语义保留）

    def test_terminal_state_written_after_mark_running(self):
        """G-S2：_mark_running(False) 先执行、终态后写——反序会把 partial/failed 覆盖回 idle。"""
        ret = {"pulled": 1, "saved": 1, "start": "20260811", "failed_dates": ["x"],
               "last_success_date": "20260811"}
        _, _, running_calls, _ = _run_sync(ret)
        assert running_calls == [True, False]   # start running → 结束清 running


class TestSyncByTradeDate:
    def _run(self, results: dict):
        """results: {YYYYMMDD: DataFrame | Exception}。"""
        from src.data_sync import engine

        def api_fn(trade_date):
            v = results[trade_date]
            if isinstance(v, Exception):
                raise v
            return v
        with patch.object(engine, "_expected_trading_days", return_value=len(results)), \
             patch.object(engine, "_adj_map_for_df", return_value={}):
            return engine._sync_by_trade_date(api_fn, lambda df: len(df), "20260811", "20260813",
                                               sleep_s=0)

    def test_empty_df_counts_as_success(self):
        """G-S4：空 df（节假日 freq=B）记成功——否则游标永久卡死在节前。"""
        r = self._run({"20260811": _df("20260811"), "20260812": pd.DataFrame(),
                       "20260813": _df("20260813")})
        assert r["failed_dates"] == []
        assert r["last_success_date"] == "20260813"

    def test_consecutive_semantics(self):
        """day1 成功 day2 失败 day3 成功 → 连续末日=day1（day3 已入库由幂等兜底重拉）。"""
        r = self._run({"20260811": _df("20260811"),
                       "20260812": Exception("boom"),
                       "20260813": _df("20260813")})
        assert len(r["failed_dates"]) == 1
        assert r["last_success_date"] == "20260811"

    def test_all_failed_returns_none(self):
        r = self._run({"20260811": Exception("a"), "20260812": Exception("b"),
                       "20260813": Exception("c")})
        assert r["last_success_date"] is None and len(r["failed_dates"]) == 3


class TestOverwriteValidation:
    def test_overwrite_rejects_garbage_freq(self):
        """G：覆盖路径此前零校验（垃圾 freq 直接建野表）。"""
        from src.data_platform import db
        with pytest.raises(AssertionError):
            db.save_bars_overwrite("2min", [tuple(range(11))])

    def test_overwrite_runs_validate_bars(self):
        from src.data_platform import db
        with patch.object(db, "validate_bars", side_effect=lambda rows: rows) as v, \
             patch.object(db, "ensure_table"), \
             patch.object(db, "get_conn", MagicMock()):
            db.save_bars_overwrite("1D", [tuple(range(11))])
        v.assert_called_once()


class TestHBlindSpots:
    """H 审要求的三个盲区 + H-S1/H-S2 修复锁死。"""

    def test_unknown_handler_no_nameerror_no_cursor_advance(self):
        """H-S1：路由表外 id——原代码 0/0 假 success 推游标 + r 未定义 NameError 双记日志。"""
        from src.data_sync import engine
        fake_cfg = {"id": "ghost_type", "name": "x", "mode": "incremental", "enabled": True,
                    "last_sync_date": "20260810", "last_sync_ts": None, "last_status": "idle"}
        updates = []
        with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
             patch.object(engine, "_get_config", return_value=fake_cfg), \
             patch.object(engine, "_HANDLERS", {}), \
             patch.object(engine, "_log"), \
             patch.object(engine, "_mark_running"), \
             patch.object(engine, "_update_sync_state",
                          side_effect=lambda *a, **k: updates.append(a)):
            result = engine.sync("ghost_type")
        assert result["status"] == "error" and "handler" in result["error"]
        assert updates == [], "未知类型不得动游标"

    def test_all_failed_null_cursor_falls_back_to_day_before_start(self):
        """H-S2：新配置（游标 NULL）首同步全失败——fallback 必须是窗口起点前一日，
        写起点本身会让下轮 start=起点+1 永久跳过起点日（off-by-one 与 F2 同构）。"""
        ret = {"pulled": 0, "saved": 0, "start": "20260811",
               "failed_dates": ["20260811:E:x"], "last_success_date": None}
        _, updates, _, _ = _run_sync(ret, cfg_last=None)
        assert updates[-1][0] == ("astock_daily", "20260810", 0, "failed")   # 0811 前一日

    def test_backfill_does_not_touch_cursor(self):
        """回补模式：不调 _update_sync_state（现状语义）。"""
        ret = {"pulled": 5, "saved": 5, "start": "20260811", "failed_dates": [],
               "last_success_date": "20260815"}
        from src.data_sync import engine
        fake_cfg = {"id": "astock_daily", "name": "x", "mode": "incremental", "enabled": True,
                    "last_sync_date": "20260818", "last_sync_ts": None, "last_status": "idle"}
        updates = []
        with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
             patch.object(engine, "_get_config", return_value=fake_cfg), \
             patch.object(engine, "_HANDLERS", {"astock_daily": MagicMock(return_value=ret)}), \
             patch.object(engine, "_log"), \
             patch.object(engine, "_mark_running"), \
             patch.object(engine, "_update_sync_state",
                          side_effect=lambda *a, **k: updates.append(a)):
            result = engine.sync("astock_daily", backfill_from="20260811")
        assert updates == [] and result["backfill"] is True

    def test_missing_column_day_does_not_advance_last_success(self):
        """缺列 continue 分支：跳过 try-else，不推进连续末日。"""
        from src.data_sync import engine
        bad = pd.DataFrame([{"oops": 1}])   # 缺 trade_date 列

        def api_fn(trade_date):
            return {"20260811": _df("20260811"), "20260812": bad,
                    "20260813": _df("20260813")}[trade_date]
        with patch.object(engine, "_expected_trading_days", return_value=3), \
             patch.object(engine, "_adj_map_for_df", return_value={}):
            r = engine._sync_by_trade_date(api_fn, lambda df: len(df), "20260811", "20260813",
                                           sleep_s=0)
        assert len(r["failed_dates"]) == 1 and "缺trade_date列" in r["failed_dates"][0]
        assert r["last_success_date"] == "20260811"   # 0812 缺列后 0813 不再推进

    def test_alert_uses_terminal_status(self):
        """H 口径统一：告警标题用终态（failed/partial 与 last_status 一致），非 sync 级 status。"""
        ret = {"pulled": 0, "saved": 0, "start": "20260811",
               "failed_dates": ["20260811:E:x"], "last_success_date": None}
        _, _, _, alerts = _run_sync(ret, cfg_last="20260810")
        assert alerts[0][1] == "failed"   # (sync_id, status, failed_dates)
