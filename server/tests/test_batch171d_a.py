"""批 171d-A · 单轮最大跨度+截断反馈守门（`flow/任务/批171d-A-单轮最大跨度与截断反馈.md` §4）。

行为级断言（D31 §1「窗口算式必须经助手」——**禁 inspect.getsource 字符串断言**，批 161 教训）：
纯函数边界 / 回落链坏值留痕 / 六站点窗口被夹（handler 级 mock）/ 游标不变式 /
透传三跳 / 增量轮零漂移。
"""
import json
from datetime import date

import pytest

from src.data_sync.engine import _clamp_span, _next_from
from src.data_sync import engine as eng


class TestPureFunctions:

    def test_clamp_leap_year_366(self):
        # 验收 1：20240101+365 天=20241231（闰年——v1 的 20250101 是 off-by-one）
        assert _clamp_span("20240101", "20261106", 366) == ("20240101", "20241231", True)

    def test_clamp_no_trunc_when_within(self):
        assert _clamp_span("20240101", "20240601", 366) == ("20240101", "20240601", False)

    def test_clamp_none_no_limit(self):
        assert _clamp_span("20240101", "20261106", None) == ("20240101", "20261106", False)

    def test_clamp_boundary_exact(self):
        # start+max_days-1 == end 恰不截（含两端）
        assert _clamp_span("20240101", "20241231", 366) == ("20240101", "20241231", False)

    def test_next_from_three_branches(self):
        assert _next_from("20241231", [], None) == "20250101"          # 无失败 ⇒ end+1
        assert _next_from("20241231", ["20240315", "20240501"], None) == "20240315"  # min(failed)
        assert _next_from("20241231", ["20240315"], "20241101") == "20241101"        # keyed 优先


class TestSpanFallback:

    def test_bad_global_value_falls_back_with_warning(self, caplog):
        """验收 7：坏值 ⇒ 回落 366 + 留痕（fail-open 必留痕，批 171 纪律）。
        步4 B-P1-3 修正：mock 断裂（_FakeCur 无 fetchone 曾走错分支）——改带 fetchone。"""
        import logging
        from src.data_sync import engine as eng
        with pytest.MonkeyPatch.context() as mp:
            class _FakeCur:
                def execute(self, sql, params=None):
                    return self

                def fetchone(self):
                    return ("abc",)   # 全局键坏值 ⇒ isdigit False ⇒ 目标分支

            class _FakeConn:
                def __enter__(self):
                    return _FakeCur()

                def __exit__(self, *a):
                    return False

            mp.setattr(eng, "get_conn", lambda *a, **k: _FakeConn())
            with caplog.at_level(logging.WARNING, logger="src.data_sync.engine"):
                v = eng._span_of(None)
        assert v == 366
        assert any("全局值非法" in r.getMessage() for r in caplog.records), "钉目标分支非 except 兜底"

    def test_task_level_override(self):
        assert _span_helper(30) == 30

    def test_task_level_zero_rejected(self):
        assert _span_helper(0) == 366   # 0=手滑非「无限制」（裁定 5 防）


def _span_helper(task_val):
    from src.data_sync import engine as eng
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(eng, "get_conn", _no_conn)
        return eng._span_of(task_val)


class _no_conn:
    def __enter__(self):
        raise RuntimeError("no db")

    def __exit__(self, *a):
        return False


class TestSiteHandlers:
    """六站点行为断言（handler 级——窗口被夹到 max_span）。mock 拉取面，真窗口算式。"""

    def _cfg(self, span):
        return {"id": "suspend_d_sync", "mode": "incremental", "enabled": True,
                "last_sync_date": None, "provider": "tushare", "_max_span_days": span}

    def test_tier1_backfill_clamped_and_cursor(self, monkeypatch):
        """验收 2/3 核心：tier1（suspend_d 出口）回补 3 年窗被夹 366 ⇒ clamped/next_from
        出具、cursor_upto == effective_end（游标不变式）。"""
        from src.data_sync import engine as eng

        monkeypatch.setattr(eng, "_read_sync_kind", lambda sid: {
            "kind": "suspend", "sub_kind": None, "pg_table": "suspend_calendar",
            "pk_cols": "{ts_code,trade_date}", "float_cols": "{}",
            "text_cols": "{suspend_type,full_day}", "rebuild": "incremental"})
        monkeypatch.setattr(eng, "_family_start", lambda *a, **k: (date(2014, 1, 1), []))
        monkeypatch.setattr(eng, "_data_ready_end_date", lambda lag=0: "20261106")

        class _FakeDF:
            def __init__(self):
                self.empty = True   # 空帧 ⇒ 每日 return 0（不触库）——纯窗口面断言

        class _FakeAdapter:
            provider = "tushare"
            venue = ""

            def fetch_supply(self, *a, **k):
                return _FakeDF()

        monkeypatch.setattr(eng, "_get_supply_adapter", lambda cfg: _FakeAdapter())
        monkeypatch.setattr(eng, "get_conn", _no_conn_raises)   # _pull_day 空帧不达 DB
        # 步4 B-P2-4：真限速器 0.3s×262 日真睡 72s——stub 限速上下文（函数内局部 import，
        # patch 源模块 rate_limit.rate_limit_context）。
        import src.data_platform.rate_limit as _rl

        class _NoRate:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(_rl, "rate_limit_context", lambda *a, **k: _NoRate())

        h = eng._HANDLERS["suspend_d_sync"]
        r = h(self._cfg(366), "20261106", backfill_from="20240101")
        assert r["clamped"] == {"floor": None, "span": True, "span_end": "20241231"}
        assert r["cursor_upto"] == "20241231", "游标不变式：截断轮游标 ≤ effective_end"
        assert r["next_from"] == "20250101"

    def test_tier1_incremental_no_clamped_key(self, monkeypatch):
        """验收 5：增量轮零漂移——尾巴窗 <<366 恒不触夹取，返回体无 clamped 键。"""
        from src.data_sync import engine as eng
        monkeypatch.setattr(eng, "_read_sync_kind", lambda sid: {
            "kind": "suspend", "sub_kind": None, "pg_table": "suspend_calendar",
            "pk_cols": "{ts_code,trade_date}", "float_cols": "{}",
            "text_cols": "{suspend_type,full_day}", "rebuild": "incremental"})
        monkeypatch.setattr(eng, "_family_start", lambda *a, **k: (date(2014, 1, 1), []))
        monkeypatch.setattr(eng, "_data_ready_end_date", lambda lag=0: "20261106")

        class _FakeDF:
            empty = True

        class _FakeAdapter:
            provider = "tushare"
            venue = ""

            def fetch_supply(self, *a, **k):
                return _FakeDF()

        monkeypatch.setattr(eng, "_get_supply_adapter", lambda cfg: _FakeAdapter())
        cfg = self._cfg(366)
        cfg["last_sync_date"] = "20261105"
        h = eng._HANDLERS["suspend_d_sync"]
        r = h(cfg, "20261106")   # 增量（无 backfill_from）
        assert "clamped" not in r and "next_from" not in r


class _no_conn_raises:
    def __enter__(self):
        raise RuntimeError("db disabled in unit test")

    def __exit__(self, *a):
        return False


class TestPassthrough:
    """透传三跳：sync() 返回体挑键（跳①）——clamped/next_from 必须过。"""

    def test_sync_return_picks_new_keys(self, monkeypatch):
        from src.data_sync import engine as eng
        monkeypatch.setattr(eng, "_get_config", lambda sid: {"id": sid, "mode": "incremental",
                                                             "enabled": True, "provider": "tushare"})
        monkeypatch.setattr(eng, "_mark_running", lambda *a: None)
        monkeypatch.setattr(eng, "_update_sync_state", lambda *a, **k: None)
        monkeypatch.setattr(eng, "_log", lambda *a, **k: None)
        monkeypatch.setattr(eng, "_sync_gap_sync", lambda *a, **k: None)
        monkeypatch.setattr(eng, "_get_supply_adapter", lambda cfg: None)

        def _handler(cfg, end_date, backfill_from=None, progress_cb=None):
            return {"pulled": 1, "saved": 1, "start": "20240101", "cursor_upto": "20241231",
                    "failed_dates": [], "expected_days": 300, "actual_days": 300,
                    "clamped": {"floor": None, "span": True}, "next_from": "20250101"}

        monkeypatch.setitem(eng._HANDLERS, "x_test", _handler)
        r = eng.sync("x_test", backfill_from="20240101")
        assert r.get("clamped") == {"floor": None, "span": True}
        assert r.get("next_from") == "20250101"

    def test_hset_payload_serializes_clamped(self, monkeypatch):
        """跳②（步4 B-P1-3 修正：真跑非自证）——mock business_redis 工厂捕获 hset mapping，
        断言 clamped 是 JSON 串且 round-trip 回原 dict（dict 直 hset 会 DataError）。
        business_redis 是工厂（socket_timeout kwargs）非实例——mock 按工厂形态。"""
        import src.scheduler.tasks as tasks_mod
        captured = {}
        _handling = {"phase": "run"}

        class _FakeR:
            def hset(self, key, mapping=None, **kw):
                if mapping.get("status") in ("done", "success"):
                    captured.update(mapping)   # 完成态 mapping（新键所在）

            def expire(self, *a, **kw):
                pass

            def delete(self, *a, **kw):
                pass

            def hgetall(self, *a, **kw):
                return {}

            def hincrby(self, *a, **kw):
                pass

        monkeypatch.setattr(tasks_mod, "business_redis",
                            lambda **kw: _FakeR())
        # 引擎侧 mock：_sync_via_celery_body 内部 import engine.sync —— patch engine 命名空间
        monkeypatch.setattr(eng, "_get_config", lambda sid: {
            "id": sid, "mode": "incremental", "enabled": True, "provider": "tushare"})
        monkeypatch.setattr(eng, "_mark_running", lambda *a: None)
        monkeypatch.setattr(eng, "_update_sync_state", lambda *a, **k: None)
        monkeypatch.setattr(eng, "_log", lambda *a, **k: None)
        monkeypatch.setattr(eng, "_sync_gap_sync", lambda *a, **k: None)
        monkeypatch.setattr(eng, "_get_supply_adapter", lambda cfg: None)
        monkeypatch.setitem(eng._HANDLERS, "x_hset",
                            lambda cfg, end_date, backfill_from=None, progress_cb=None: {
                                "pulled": 1, "saved": 1, "cursor_upto": "20241231",
                                "failed_dates": [], "expected_days": 1, "actual_days": 1,
                                "clamped": {"floor": None, "span": True},
                                "next_from": "20250101"})
        from src.data_sync.engine import sync as _engine_sync
        monkeypatch.setattr("src.data_sync.engine.sync", _engine_sync)
        tasks_mod._sync_via_celery_body(
            type("S", (), {"request": type("R", (), {"id": "t-hset"})})(),
            "x_hset", backfill_from="20240101", track=False)
        assert isinstance(captured.get("clamped"), str), "hset 值必须标量（str）"
        assert json.loads(captured["clamped"]) == {"floor": None, "span": True}
        assert captured.get("next_from") == "20250101"

    def test_keyed_incremental_truncation_cursor(self, monkeypatch):
        """步4 双审 P0 守门（A/B 同撞）：keyed 族增量首跑截断零失败 ⇒ 游标推 effective_end
        而非 today——`:445` 消费 cursor_upto 键（修复前直用 end_date 越窗）。"""
        from src.data_sync import engine as eng
        monkeypatch.setattr(eng, "_get_config", lambda sid: {"id": sid, "mode": "incremental",
                                                             "enabled": True, "provider": "tushare"})
        updates = {}
        monkeypatch.setattr(eng, "_mark_running", lambda *a: None)
        monkeypatch.setattr(eng, "_update_sync_state",
                            lambda sid, last, count, status="idle": updates.update(
                                sid=sid, last=last, status=status))
        monkeypatch.setattr(eng, "_log", lambda *a, **k: None)
        monkeypatch.setattr(eng, "_sync_gap_sync", lambda *a, **k: None)
        monkeypatch.setattr(eng, "_get_supply_adapter", lambda cfg: None)

        def _handler(cfg, end_date, backfill_from=None, progress_cb=None):
            # keyed 形态：拉到夹后 eff=20141231 全成功（lsd=eff 零失败）
            return {"pulled": 1, "saved": 1, "start": "20140101",
                    "last_success_date": "20141231",
                    "cursor_upto": "20141231",   # ← 修复链：handler 出具夹后界
                    "failed_dates": [], "expected_days": 300, "actual_days": 300}

        monkeypatch.setitem(eng._HANDLERS, "x_keyed", _handler)
        eng.sync("x_keyed")   # 增量（无 backfill）——end_date=today 语境
        assert updates["last"] == "20141231", "游标必须收敛 effective_end（修复前=today 越窗）"
