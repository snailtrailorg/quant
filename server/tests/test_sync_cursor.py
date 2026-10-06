"""F2 根因收尾测试（G 审 6 场景）：游标三态 + 连续成功末日 + 空 df 成功 + 顺序锁死 + overwrite 校验。

背景：a28a5fa 起 "1D" 断言炸 → 逐日异常吞进 failed_dates → 游标照推 end_date → 日线静默断 11 天。
修复语义：返回 last_success_date 键的 handler 才走三态（全失败不动/部分失败推连续末日/全成功推末）；
其余 handler 无条件推进（分钟线 per-symbol 失败粒度不能被卷入三态——200 积分全失败会冻游标重试风暴）。
"""
from datetime import date
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
         patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
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
        """无 last_success_date 键的 handler（分钟线 per-symbol 失败粒度）：有失败也照推**游标**——
        防重试风暴（G-S1）。**批 104**：游标推进语义不变，但**终态词如实**记 failed——
        原实现恒给 'idle' ⇒ `sync_config.last_status` 与失败告警标题双双掩盖失败。"""
        ret = {"pulled": 0, "saved": 0, "start": "20260811",
               "failed_dates": ["000001.SZ:Error:积分不足"]}   # 分钟线失败粒度=per-symbol
        _, updates, _, alerts = _run_sync(ret)
        assert updates[-1][0][1] == date.today().strftime("%Y%m%d")   # 游标仍无条件推进
        assert updates[-1][0][3] == "failed"        # 批 104：终态词如实（原为 'idle'）
        assert alerts and alerts[-1][1] == "failed", alerts            # 告警标题不再写 'idle'

    def test_handler_without_key_partial_when_some_saved(self):
        """**批 104**：无 last_success_date 键 + 有失败但**也有入库** ⇒ partial（非 idle、非 failed）。"""
        ret = {"pulled": 5, "saved": 5, "start": "20260811",
               "failed_dates": ["000001.SZ:Error:积分不足"]}
        _, updates, _, alerts = _run_sync(ret)
        assert updates[-1][0] == ("astock_daily", date.today().strftime("%Y%m%d"), 5, "partial")
        assert alerts and alerts[-1][1] == "partial", alerts

    def test_handler_without_key_no_failure_stays_idle(self):
        """**批 104 回归钉**：无失败时终态词仍是 idle（本次改动**不动**健康路径的词）。"""
        ret = {"pulled": 3, "saved": 3, "start": "20260811", "failed_dates": []}
        _, updates, _, alerts = _run_sync(ret)
        assert updates[-1][0][3] == "idle"
        assert not alerts

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
        with patch.object(engine, "_expected_trading_days", return_value=len(results)):
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
             patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
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
             patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
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
        with patch.object(engine, "_expected_trading_days", return_value=3):
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


def _run_sync_raises(cfg_last="20260925", backfill=False):
    """跑 engine.sync()，**handler 抛异常**（非返回型失败）。返回 (result, updates, running, alerts)。

    P1-B 修复锁（2026-10-03 数据同步验证）：原 except 分支只 _mark_running(False)、
    不写终态 ⇒ last_sync_ts 停旧值 ⇒ 调度器 base 恒旧 ⇒ 每 300s 重触发（重试风暴）。
    """
    from src.data_sync import engine
    fake_cfg = {"id": "concept_sync", "name": "x", "mode": "incremental", "enabled": True,
                "last_sync_date": cfg_last, "last_sync_ts": None, "last_status": "idle"}
    update_calls, running_calls, alerts = [], [], []

    def _boom(*a, **k):
        raise RuntimeError("Error 1054 (42S22): Unknown column 'name' in 'field list'")

    with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
         patch.object(engine, "_get_config", return_value=fake_cfg), \
         patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
         patch.object(engine, "_HANDLERS", {"concept_sync": _boom}), \
         patch.object(engine, "_log"), \
         patch.object(engine, "_mark_running",
                      side_effect=lambda sid, running: running_calls.append(running)), \
         patch.object(engine, "_update_sync_state",
                      side_effect=lambda *a, **k: update_calls.append((a, k))), \
         patch.object(engine, "_alert_sync_failure",
                      side_effect=lambda *a, **k: alerts.append(a)):
        result = engine.sync("concept_sync",
                             **({"backfill_from": "20260811"} if backfill else {}))
    return result, update_calls, running_calls, alerts


class TestExceptionPathBackoff:
    """P1-B（2026-10-03 数据同步验证）：handler 抛异常同样推进 last_sync_ts。

    原缺陷（G-S3 覆盖漏洞）：except 分支只 _mark_running(False)，last_sync_ts 停旧值
    ⇒ 调度器 base 恒旧 ⇒ croniter.get_next 恒返回已过去的到点 ⇒ 每 300s 重触发。
    实证：concept_sync 上游 Error 1054 后 ts 停 2026-09-25、sync_log 积 1876 条。
    """

    def test_exception_writes_terminal_state_with_old_cursor(self):
        """异常：必须写终态（=刷新 ts）、游标写回**旧值**（不动）、status=failed。"""
        result, updates, _, _ = _run_sync_raises(cfg_last="20260925")
        assert result["status"] == "error" and "1054" in result["error"]
        assert len(updates) == 1, "异常路径必须写终态——否则 last_sync_ts 不刷新=重试风暴"
        assert updates[0][0] == ("concept_sync", "20260925", 0, "failed")   # 游标=旧值

    def test_exception_null_cursor_stays_null(self):
        """新配置游标 NULL 时异常：写回 None（不变），绝不写空串污染游标。"""
        _, updates, _, _ = _run_sync_raises(cfg_last=None)
        assert updates[0][0] == ("concept_sync", None, 0, "failed")

    def test_exception_mark_running_order(self):
        """G-S2 顺序在异常路径同样成立：先 True（running）后 False（清），终态随后覆盖。"""
        _, _, running, _ = _run_sync_raises()
        assert running == [True, False]

    def test_exception_alerts_failure(self):
        """异常型失败补主动告警（status=failed）——P1-A 被埋 1873 条无人知的直接原因。"""
        _, _, _, alerts = _run_sync_raises()
        assert alerts and alerts[0][1] == "failed"

    def test_backfill_exception_does_not_touch_cursor(self):
        """回补模式异常：不写游标/ts（与返回路径 _advance=None 同源语义）。"""
        _, updates, _, _ = _run_sync_raises(backfill=True)
        assert updates == []
