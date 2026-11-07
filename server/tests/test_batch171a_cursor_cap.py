"""批 171a·D1 → 171 收官件：cursor_cap 全链退役反向守门。

D1 代码链已删（171 收官件：出具/痊愈 DEL/逃逸闸三函数+常量+sync() 消费段+四出具站点+
RUNBOOK 词条）。本文件钉「**死码不得复活**」——不是行为测试（行为在 test_sync_cursor /
test_batch171d_c），是防腐化断言：

- `_CAP_EMIT_IDS` 恒空（有人回填白名单即红——D1 复活的第一步就是它）；
- 退役符号在 engine 中**不存在**（重定义即红）；
- sync() 对派生族 stray `cursor_cap` 键恒忽略（消费侧防线）；
- RUNBOOK `sync.cursor_frozen` 词条已删（永不触发的死档不得回归）；
- 12 派生族+清单调集恒等（防清单漂移）。

历史：原 23 用例（cap 出具四条件/收口三分支/逃逸闸）随函数删除退役——消费链行为钉
保留在 test_sync_cursor（三态）与 test_batch171d_c（派生收口/stray 键）。
"""
import pytest

from src.data_sync import engine

NS = "test171d1"


class _Cap:
    def __init__(self):
        self.states = []   # (date, saved, term)
        self.logs = []     # (status, error/note)
        self.freeze_calls = []


def _run(handler_ret, *, cfg=None, backfill=False):
    """跑 engine.sync() 全路径（派生族 _local_max mock None——本文件不测派生行为）。"""
    from unittest.mock import MagicMock, patch

    cap = _Cap()
    sid = (cfg or {}).get("id", NS)
    fake_cfg = cfg or {"id": NS, "name": "x", "mode": "incremental", "enabled": True,
                       "last_sync_date": "20261010", "last_sync_ts": None, "last_status": "idle"}

    class _FakeLock:
        acquired = True

        def __init__(self, sid):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_freeze(sid, *, bumped):
        cap.freeze_calls.append((sid, bumped))
        return None

    with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
         patch.object(engine, "_get_config", return_value=fake_cfg), \
         patch.object(engine, "_HANDLERS", {sid: MagicMock(return_value=handler_ret)}), \
         patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
         patch.object(engine, "_local_max", return_value=None), \
         patch.object(engine, "_mark_running"), \
         patch.object(engine, "_update_sync_state",
                      side_effect=lambda s, d, n, t: cap.states.append((str(d), n, t))), \
         patch.object(engine, "_log",
                      side_effect=lambda *a, **k: cap.logs.append((a[7], a[8]))), \
         patch.object(engine, "_alert_generic"), \
         patch.object(engine, "_alert_sync_failure"):
        result = engine.sync(sid, backfill_from="20261001" if backfill else None)
    return result, cap


class TestRetiredSymbols:
    """退役符号不存在断言（重定义即红——D1 复活的形态面）。"""

    def test_functions_gone(self):
        assert not hasattr(engine, "_cursor_cap"), "出具函数已删，不得复活"
        assert not hasattr(engine, "_cursor_cap_healed"), "痊愈判定已删"
        assert not hasattr(engine, "_cursor_freeze_count"), "逃逸闸计数已删"
        assert not hasattr(engine, "_CAP_FREEZE_ALERT_ROUNDS"), "逃逸闸常量已删"

    def test_whitelist_stays_empty(self):
        assert engine._CAP_EMIT_IDS == frozenset(), \
            "D1 退役：白名单恒空（回填=D1 复活第一步）"

    def test_runbook_entry_gone(self):
        from src.alert_notify.runbook import RUNBOOK
        assert "sync.cursor_frozen" not in RUNBOOK, "死档词条已删（永不触发）"


class TestDerivedRegistry:
    """12 族清单+装配可达（171d-C d1 职责承接——防字面量集与路由表漂移）。"""

    def test_twelve_families_exact(self):
        assert engine._DERIVED_CURSOR_IDS == frozenset({
            "astock_daily", "listed_fund_daily", "cb_daily", "astock_daily_jq",
            "stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "st_list_sync",
            "cyq_perf_sync", "index_daily", "astock_minute", "astock_minute_5min",
        }), "派生 12 族（D31 §3 分轨表稠密轨）"
        assert set(engine._DERIVED_MAX_SQL) == set(engine._DERIVED_CURSOR_IDS), \
            "SQL 声明与派生集恒等"

    def test_derived_subset_of_reachable(self):
        assert engine._DERIVED_CURSOR_IDS <= (set(engine._VIA_KIND_IDS)
                                              | set(engine._HANDLERS)), \
            "派生集 ⊆ 装配可达（via_kind ∪ _HANDLERS）"


class TestStrayCapKeyIgnored:
    """消费侧防线（171d-C d2 职责承接）：派生族 stray cursor_cap 键恒忽略。"""

    def test_stray_key_no_clamp_no_note(self):
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                        "expected_days": 5, "actual_days": 5,
                        "last_success_date": "20261015",
                        "reconcile": {"gap_dates": ["20261014"]},
                        "cursor_cap": "20261013"},   # stray 键——D1 复活尝试
                       cfg={"id": "astock_daily", "name": "x", "mode": "incremental",
                            "enabled": True, "last_sync_date": "20261010",
                            "last_sync_ts": None, "last_status": "idle"})
        assert all("cursor_capped" not in (e or "") for _s, e in cap.logs), "无 cap 注记"
        assert cap.states[0][0] != "20261013", "stray cap 不得收口推进值"

    def test_backfill_round_still_no_touch(self):
        """回补轮零游标触碰（现状语义回归钉——原 c8 职责）。"""
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                        "expected_days": 5, "actual_days": 5,
                        "last_success_date": "20261015"},
                       cfg={"id": "astock_daily", "name": "x", "mode": "incremental",
                            "enabled": True, "last_sync_date": "20261010",
                            "last_sync_ts": None, "last_status": "idle"},
                       backfill=True)
        assert cap.states == [], "回补不推游标"
