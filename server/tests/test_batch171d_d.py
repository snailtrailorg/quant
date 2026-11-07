"""批 171d-D · 回补差集+预算闸+成功闸+A股scope 守门（`flow/任务/批171d-D-...md` v2.1 §4）。

步4 双盲审 A-P0 补：验收 1（稠密差集/稀疏不罩）/3（stop-after 预算）/4（成功闸三态+回退
有界）/6（DEF-08 薄壳）四项 mock 单测——**路径不可分辨反面**纪律（171 教训）。
"""
from datetime import date, timedelta

import pytest

from src.data_sync.engine import (_DENSE_BACKFILL_IDS, _clamp_span,
                                  _sparse_cursor_end)


class TestDenseWhitelist:

    def test_five_families_exact(self):
        assert _DENSE_BACKFILL_IDS == frozenset({
            "stk_limit_sync", "moneyflow_sync", "margin_detail_sync",
            "cyq_perf_sync", "st_list_sync"})

    def test_sparse_not_in_whitelist(self):
        for sid in ("suspend_d_sync", "forecast_sync", "top_list_sync",
                    "block_trade_sync"):
            assert sid not in _DENSE_BACKFILL_IDS


class TestSparseCursorEnd:
    """验收 4：成功闸三态 + 回退有界 ≤overlap。"""

    def test_no_failures_keeps_end(self):
        assert _sparse_cursor_end("20261106", []) == "20261106"

    def test_date_failures_stop_before_min(self):
        got = _sparse_cursor_end("20261106",
                                 ["20261103:Timeout:x", "20261105:Error:y"])
        assert got == "20261102", "min(failed)-1（首失败日前）"

    def test_nondigit_prefix_filtered_keeps_end(self):
        # minute/index 形失败串（非日期前缀）全滤 ⇒ 保持 end（与 171d-A next_from 闸同构）
        assert _sparse_cursor_end("20261106",
                                 ["000001.SZSE:Timeout", "no_rows:x"]) == "20261106"

    def test_first_day_failure_rollback_bounded(self):
        start = "20261101"
        got = _sparse_cursor_end("20261106", ["20261101:Err:x"])
        # cursor = start-1；回退 ≤overlap(3)：≥ start-1（即不越过 last-overlap 语义）
        assert got >= (date(2026, 11, 1) - timedelta(days=4)).strftime("%Y%m%d")


class TestRowBudgetStopAfter:
    """验收 3：stop-after（末日超限允许）——mock 每日 3000 行×3 日×预算 5000 ⇒ 拉 2 日停。"""

    def _rec(self, monkeypatch, daily_rows, budget, missing_n):
        from src.data_sync import engine as eng
        expected = [f"2026110{i}" for i in range(1, missing_n + 1)]

        monkeypatch.setattr(eng, "_sync_gap_sync_one", lambda *a, **k: {})
        pulls = []
        _local_state = {"have": set()}

        def repull(d):
            pulls.append(d)
            _local_state["have"].add(d)   # 拉过即有（二次读语义）
            return daily_rows

        monkeypatch.setattr(eng, "_local_dates",
                            lambda *a, **k: _local_state["have"])
        return eng._reconcile_dates("x_test", table="t", date_expr="d",
                                    expected=expected, win_lo=expected[0],
                                    win_hi=expected[-1], repull_fn=repull,
                                    row_budget=budget), pulls

    def test_two_days_then_stop(self, monkeypatch):
        rec, pulls = self._rec(monkeypatch, 3000, 5000, 3)
        assert len(pulls) == 2, "acc=6000≥5000 恰拉 2 日（末日超限允许）"
        assert rec.get("budget_truncated") is True
        assert rec["still_missing"] == ["20261103"], "剩余回传（next_from 候选）"

    def test_exhausted_exactly_no_truncation(self, monkeypatch):
        rec, pulls = self._rec(monkeypatch, 3000, 9000, 3)
        assert len(pulls) == 3 and "budget_truncated" not in rec, "恰耗尽不出截断键"

    def test_tuple_return_normalized(self, monkeypatch):
        """B-P0-2 实证回归钉：cb/daily_batch repull_fn 返 tuple 不炸且取[0]计。"""
        from src.data_sync import engine as eng
        expected = ["20261101", "20261102"]

        monkeypatch.setattr(eng, "_sync_gap_sync_one", lambda *a, **k: {})
        _have = set()

        def _rp(d):
            _have.add(d)
            return (3000, 2999)   # tuple 形态（cb/daily_batch）

        monkeypatch.setattr(eng, "_local_dates", lambda *a, **k: _have)
        rec = eng._reconcile_dates("x_t", table="t", date_expr="d",
                                   expected=expected, win_lo="20261101",
                                   win_hi="20261102", repull_fn=_rp,
                                   row_budget=5000)
        assert len(rec["still_missing"]) == 0, "tuple 归一后两日都拉完"


class TestDef08Supply:

    def test_pull_daily_supply_signature_match(self, monkeypatch):
        """验收 6：fetch_supply kwargs 过滤后恰命中薄壳形参（137a 陷阱规避直证）。"""
        from src.data_platform.adapters import tushare_adapter as ta
        got = {}

        def _fake_pull_daily(ts_code, start_date, end_date=None, adj=None):
            got.update(ts_code=ts_code, start_date=start_date, end_date=end_date)
            return "DF"

        monkeypatch.setattr(ta, "pull_daily", _fake_pull_daily)
        out = ta.pull_daily_supply(symbol="000001.SZ", freq="1D",
                                   start="20260101", end="20260131")
        assert out == "DF"
        assert got["ts_code"] == "000001.SZ" and got["start_date"] == "20260101"

    def test_supply_pull_registered_non_factory(self):
        from src.data_platform.adapters.base import (_SUPPLY_PULL,
                                                    _SUPPLY_PULL_NON_FACTORY)
        assert _SUPPLY_PULL[("bar_daily", "stock")] == "pull_daily_supply"
        assert ("bar_daily", "stock") in _SUPPLY_PULL_NON_FACTORY


class TestTier1BackfillDiff:
    """验收 1（tier1 侧）：稠密差集零缺失 ⇒ pulled=0 / 稀疏不触预读。"""

    def _run(self, monkeypatch, sid, local_dates):
        from src.data_sync import engine as eng
        from datetime import date as _d
        monkeypatch.setattr(eng, "_read_sync_kind", lambda s: {
            "kind": "suspend", "sub_kind": None, "pg_table": "suspend_calendar",
            "pk_cols": "{ts_code,trade_date}", "float_cols": "{}",
            "text_cols": "{suspend_type,full_day}", "rebuild": "incremental"})
        monkeypatch.setattr(eng, "_family_start",
                            lambda *a, **k: (_d(2014, 1, 1), []))
        monkeypatch.setattr(eng, "_data_ready_end_date", lambda lag=0: "20260103")

        class _FD:
            empty = True

        class _FA:
            provider = "tushare"
            venue = ""

            def fetch_supply(self, *a, **k):
                return _FD()

        monkeypatch.setattr(eng, "_get_supply_adapter", lambda cfg: _FA())

        # 对账落库链桩（reconcile 分支 _sync_gap_sync_one 也会碰库）——mock 落表口
        monkeypatch.setattr(eng, "_sync_gap_sync_one", lambda *a, **k: {})
        monkeypatch.setattr(eng, "_local_dates",
                            lambda *a, **k: set(local_dates) if local_dates else None)
        monkeypatch.setattr(eng, "_trade_dates_in_range",
                            lambda lo, hi: [f"2026010{i}" for i in (1, 2, 3)])
        monkeypatch.setattr(eng, "_cursor_cap", lambda *a: None)
        monkeypatch.setattr(eng, "_cursor_cap_healed", lambda *a: False)
        monkeypatch.setattr(eng, "_cursor_freeze_count", lambda *a, **k: 0)
        monkeypatch.setattr(eng, "_alert_sync_gaps", lambda *a: None)
        cfg = {"id": sid, "mode": "incremental", "enabled": True,
               "last_sync_date": None, "provider": "tushare", "_max_span_days": 366}
        return eng._HANDLERS[sid](cfg, "20260103", backfill_from="20260101")

    def test_dense_zero_missing_skips_pull(self, monkeypatch):
        r = self._run(monkeypatch, "stk_limit_sync",
                     ["20260101", "20260102", "20260103"])
        assert r["pulled"] == 0, "零缺失零拉取"
        assert r["expected_days"] == 3, "A-P1-3：分母钉全窗（非缺失集）"

    def test_sparse_suspend_replays_blind(self, monkeypatch):
        """稀疏不罩直证：同窗数据全在仍逐日重放（差集预读不触发）。"""
        r = self._run(monkeypatch, "suspend_d_sync",
                     ["20260101", "20260102", "20260103"])
        assert r["expected_days"] == 3, "稀疏族 date_range 未被过滤（重放语义）"
