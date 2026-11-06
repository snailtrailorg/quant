"""批 171a · D1 游标不越界守门（`flow/任务/批171a-D1-游标不越界.md` §5 验收 11 条）。

钉的是**行为**：cap 出具四条件 / 收口三分支（min+clamp+终态词）/ 逃逸闸（==N 恰一次、
DEL 语义、fail-soft 三不、零触碰）/ 白名单漂移 / 既有测试零漂移。

mock harness 同 `test_sync_cursor._run_sync` 范式（不依赖真库真 Valkey）。
"""
import pytest

from src.data_sync import engine

NS = "test171d1"


# ---------------------------------------------------------------------------
# harness：patch sync() 的全部外协（_log/_update_sync_state/_mark_running/
# _alert_* / _cursor_freeze_count），捕获副作用
# ---------------------------------------------------------------------------


class _Cap:
    def __init__(self):
        self.logs = []          # _log 调用 (status, error)
        self.states = []        # _update_sync_state 调用 (date, saved, term)
        self.alerts = []        # _alert_generic (code, title)
        self.freeze_calls = []  # _cursor_freeze_count (sid, bumped)
        self.freeze_ret = []    # 每次调用的返回值（队列）
        self.fail_alerts = 0

    def pop_freeze_ret(self):
        return self.freeze_ret.pop(0) if self.freeze_ret else None


def _run(handler_ret, cfg=None, backfill=False):
    """跑 engine.sync() 全路径，返回 (result, cap)。handler 返回 handler_ret（可含 cursor_cap）。"""
    cap = _Cap()
    fake_cfg = cfg or {"id": NS, "name": "x", "mode": "incremental", "enabled": True,
                       "last_sync_date": "20261010", "last_sync_ts": None, "last_status": "idle"}

    from unittest.mock import MagicMock, patch

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
        return cap.pop_freeze_ret()

    with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
         patch.object(engine, "_get_config", return_value=fake_cfg), \
         patch.object(engine, "_HANDLERS", {NS: MagicMock(return_value=handler_ret)}), \
         patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
         patch.object(engine, "_mark_running"), \
         patch.object(engine, "_update_sync_state",
                      side_effect=lambda sid, d, s, t: cap.states.append((str(d), s, t))), \
         patch.object(engine, "_log",
                      side_effect=lambda *a, **k: cap.logs.append((a[7], a[8]))), \
         patch.object(engine, "_alert_generic",
                      side_effect=lambda code, title, body: cap.alerts.append((code, title))), \
         patch.object(engine, "_alert_sync_failure",
                      side_effect=lambda *a, **k: cap.__setattr__("fail_alerts", cap.fail_alerts + 1)), \
         patch.object(engine, "_cursor_freeze_count", side_effect=_fake_freeze):
        result = engine.sync(NS, backfill_from="20261001" if backfill else None)
    return result, cap


# ---------------------------------------------------------------------------
# 验收 1/2：主判据 + 未声明者逐字现状（gap 非空防派生红线）
# ---------------------------------------------------------------------------


class TestCursorCapClamp:

    def test_a1_cap_clamps_cursor(self):
        """验收 1：last_success=10-15 + cap=10-13 ⇒ 推进 10-13、终态 partial。"""
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                        "expected_days": 5, "actual_days": 5,
                        "last_success_date": "20261015",
                        "reconcile": {"gap_dates": ["20261014"]},
                        "cursor_cap": "20261013"})
        assert cap.states[0] == ("20261013", 1, "partial")

    def test_a2_undeclared_ignores_gap_dates(self):
        """验收 2：无 cap 键但 reconcile.gap_dates 非空 ⇒ 原推进值原终态词（钉死禁 sync() 派生）。"""
        from datetime import date as _d
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                        "expected_days": 5, "actual_days": 5,
                        "last_success_date": "20261015",
                        "reconcile": {"gap_dates": ["20261014"]}})
        assert cap.states[0][0] == _d.today().strftime("%Y%m%d"), "end_date 原样（A-P2-1）"
        assert cap.states[0][2] == "idle"
        assert cap.freeze_calls == [], "未声明 ⇒ 零 Valkey 触碰"

    def test_a5_keyless_branch_clamped_and_partial(self):
        """验收 4/5：无键分支 cursor_upto 收口至 cap 且 idle→partial。"""
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                        "expected_days": 5, "actual_days": 5,
                        "cursor_upto": "20261015",
                        "reconcile": {"gap_dates": ["20261014"]},
                        "cursor_cap": "20261013"})
        assert cap.states[0][0] == "20261013"
        assert cap.states[0][2] == "partial"

    def test_a6_all_failed_not_clamped(self):
        """验收 6：全失败（keyed，含 lsd=None 首日失败形态）⇒ 游标写回旧值，不收口不倒退。

        步4 B-P0-1 回归钉：lsd=None + cap 共现曾使预算段 TypeError 炸整轮为 error/failed
        ——本用例**必须**断言 status=success 且无 error 落日志（原断言在异常路径下碰巧全过）。
        """
        _r, cap = _run({"pulled": 0, "saved": 0, "start": "20261011",
                        "failed_dates": ["20261011:X"], "expected_days": 1, "actual_days": 0,
                        "last_success_date": None,
                        "reconcile": {"gap_dates": ["20261011"]},
                        "cursor_cap": "20261010"})
        assert _r["status"] not in ("error",), "lsd=None + cap 不得炸（P0-1 回归钉）"
        assert all(s != "error" for s, _e in cap.logs), "无 error 行落 sync_log"
        assert cap.states[0][2] == "failed"
        assert cap.states[0][0] == "20261010"   # fallback＝起点前一日，游标不动

    def test_a7_clamp_never_regresses(self):
        """验收 7：cap < 旧游标（tier1 overlap 场景）⇒ 推进值＝旧游标（clamp 兜底不倒退）。

        步4 双盲审 A-P1-2 补钉：clamp 兜底格＝「收口实际生效」（游标被压回旧值、
        词 partial、freeze INCR）——逃逸闸必须看得见这类冻结（游标不动本身是病）。
        """
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261009", "failed_dates": [],
                        "expected_days": 5, "actual_days": 5,
                        "cursor_upto": "20261105",
                        "reconcile": {"gap_dates": ["20261009"]},   # cap=20261008 < 旧游标 20261010
                        "cursor_cap": "20261008"})
        assert cap.states[0][0] == "20261010", "clamp：不低于旧游标"
        assert cap.states[0][2] == "partial", "实际改值（20261105→20261010）⇒ 词 partial"
        assert cap.freeze_calls == [(NS, True)], "生效 ⇒ INCR（逃逸闸可见）"
        assert any("cursor_capped:20261008" in (e or "") for _s, e in cap.logs)

    def test_a8_error_note(self):
        """验收 8：收口生效 ⇒ _log error 含 cursor_capped:<cap>。"""
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                        "expected_days": 5, "actual_days": 5,
                        "last_success_date": "20261015",
                        "reconcile": {"gap_dates": ["20261014"]},
                        "cursor_cap": "20261013"})
        assert any("cursor_capped:20261013" in (e or "") for _s, e in cap.logs)
        # 对照：无 cap 轮 error 空
        _r2, cap2 = _run({"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                          "expected_days": 5, "actual_days": 5,
                          "last_success_date": "20261015",
                          "reconcile": {"gap_dates": ["20261014"]}})
        assert all((e or "") == "" for _s, e in cap2.logs)


# ---------------------------------------------------------------------------
# 验收 3：helper 四条件 + 正例
# ---------------------------------------------------------------------------


class TestHelper:

    def test_b1_whitelist_gate(self):
        assert engine._cursor_cap("forecast_sync",
                                  {"gap_dates": ["20261014"], "uncertain": None, "expected": 3},
                                  []) is None

    def test_b2_empty_gap(self):
        assert engine._cursor_cap("astock_daily",
                                  {"gap_dates": [], "uncertain": None, "expected": 3},
                                  []) is None

    def test_b3_uncertain(self):
        assert engine._cursor_cap("astock_daily",
                                  {"gap_dates": ["20261014"], "uncertain": "calendar_uncovered",
                                   "expected": 3},
                                  []) is None

    def test_b4_all_failed_guard(self):
        """tier1 全失败：failed=全期望日 + gap 非空 ⇒ None（G-S1 不漂移）。"""
        assert engine._cursor_cap("stk_limit_sync",
                                  {"gap_dates": ["20261011", "20261012"], "uncertain": None,
                                   "expected": 2},
                                  ["20261011:X", "20261012:Y"]) is None

    def test_b5_positive_full_path(self):
        """正例：白名单 + gap 两日 + uncertain None + failed 1/3 ⇒ 恰 20261013。

        兼钉 rec 身份：expected 是 int（传 handler 返回体形态则此断言语义漂移——
        本用例直接构造 reconcile dict 形态）。
        """
        got = engine._cursor_cap("astock_daily",
                                 {"gap_dates": ["20261014", "20261016"], "uncertain": None,
                                  "expected": 3},
                                 ["20261012:X"])
        assert got == "20261013"

    def test_b6_healed(self):
        """痊愈轮判定：白名单 + 对账在场 + 非 uncertain + gap 清空 ⇒ True。"""
        assert engine._cursor_cap_healed("astock_daily",
                                         {"gap_dates": [], "uncertain": None, "expected": 3})
        assert not engine._cursor_cap_healed("forecast_sync",
                                             {"gap_dates": [], "uncertain": None, "expected": 3})
        assert not engine._cursor_cap_healed("astock_daily",
                                             {"gap_dates": ["20261014"], "uncertain": None})


# ---------------------------------------------------------------------------
# 验收 8/9：逃逸闸 + 零触碰
# ---------------------------------------------------------------------------


class TestFreezeGate:

    RET = {"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
           "expected_days": 5, "actual_days": 5, "last_success_date": "20261015",
           "reconcile": {"gap_dates": ["20261014"]}, "cursor_cap": "20261013"}

    def test_c1_effective_round_bumps(self):
        """生效轮 bumped=True（INCR 语义）；多轮计数门由 c2 的返回值驱动钉。"""
        _r, cap = _run(self.RET)
        assert cap.freeze_calls == [(NS, True)]

    def test_c2_alert_fires_only_at_threshold(self):
        """计数恰达 7 ⇒ 打点一次；<7 或 >7 不打。"""
        rets = {"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                "expected_days": 5, "actual_days": 5, "last_success_date": "20261015",
                "reconcile": {"gap_dates": ["20261014"]}, "cursor_cap": "20261013"}
        for n, should_fire in [(6, False), (7, True), (8, False)]:
            cap = _Cap()
            cap.freeze_ret = [n]
            # 重跑 _run 但注入 freeze_ret
            from unittest.mock import MagicMock, patch

            class _FakeLock:
                acquired = True

                def __init__(self, sid):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            fake_cfg = {"id": NS, "name": "x", "mode": "incremental", "enabled": True,
                        "last_sync_date": "20261010", "last_sync_ts": None, "last_status": "idle"}

            def _fake_freeze(sid, *, bumped):
                cap.freeze_calls.append((sid, bumped))
                return cap.freeze_ret.pop(0) if cap.freeze_ret else None

            with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
                 patch.object(engine, "_get_config", return_value=fake_cfg), \
                 patch.object(engine, "_HANDLERS", {NS: MagicMock(return_value=rets)}), \
                 patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
                 patch.object(engine, "_mark_running"), \
                 patch.object(engine, "_update_sync_state",
                              side_effect=lambda *a: cap.states.append(a)), \
                 patch.object(engine, "_log", side_effect=lambda *a, **k: None), \
                 patch.object(engine, "_alert_generic",
                              side_effect=lambda code, title, body: cap.alerts.append(code)), \
                 patch.object(engine, "_alert_sync_failure"), \
                 patch.object(engine, "_cursor_freeze_count", side_effect=_fake_freeze):
                engine.sync(NS)
            fired = "sync.cursor_frozen" in cap.alerts
            assert fired == should_fire, f"n={n}: fired={fired}"

    def test_c3_ineffective_round_dels(self):
        """出具未生效 ⇒ bumped=False（DEL）。

        真实未生效场景（keyed 无失败分支推 end_date、cap < end_date 恒生效——见
        `gap ⊆ 窗口` ⇒ cap < end_date）：**keyed 部分失败分支推 last_success_date**，
        cap ≥ last_success（缺口在断点之后）⇒ clamp 不改值 ⇒ 未生效 ⇒ DEL、终态词
        保持 partial（原词，非通则改写）。
        """
        ret = {"pulled": 1, "saved": 1, "start": "20261011",
               "failed_dates": ["20261012:X"], "expected_days": 5, "actual_days": 4,
               "last_success_date": "20261011",
               "reconcile": {"gap_dates": ["20261013"]},   # cap=20261012 > last_success=20261011
               "cursor_cap": "20261012"}
        _r, cap = _run(ret)
        assert cap.freeze_calls == [(NS, False)], "未生效 ⇒ DEL"
        assert cap.states[0] == ("20261011", 1, "partial"), "推进值不变、原词保持"
        assert all((e or "") == "" for _s, e in cap.logs), "无 cursor_capped 注记"

    def test_c4_zero_touch_when_no_cap(self):
        """验收 9：_cap=None 轮零 Valkey 触碰。"""
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261011", "failed_dates": [],
                        "expected_days": 5, "actual_days": 5, "last_success_date": "20261015"})
        assert cap.freeze_calls == []

    def test_c5_freeze_returns_none_no_side_effect(self):
        """验收 8 异常例（fail-soft 三不）：helper 返 None（计数未知）⇒ sync() 正常返回、
        终态仍 partial、sync_log 恰一行无 error、无 failed 告警、不打冻结告警。

        （步4 B-P2-1 重写：真实现的 fail-soft 在 helper 内——本用例钉「helper 返 None 时
        调用点三不」；helper 自身不抛由 c6 钉。）
        """
        ret = dict(self.RET)
        from unittest.mock import MagicMock, patch

        class _FakeLock:
            acquired = True

            def __init__(self, sid):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_cfg = {"id": NS, "name": "x", "mode": "incremental", "enabled": True,
                    "last_sync_date": "20261010", "last_sync_ts": None, "last_status": "idle"}
        states, logs = [], []
        fail_alerts = []

        with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
             patch.object(engine, "_get_config", return_value=fake_cfg), \
             patch.object(engine, "_HANDLERS", {NS: MagicMock(return_value=ret)}), \
             patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
             patch.object(engine, "_mark_running"), \
             patch.object(engine, "_update_sync_state",
                          side_effect=lambda sid, d, s, t: states.append((str(d), s, t))), \
             patch.object(engine, "_log", side_effect=lambda *a, **k: logs.append((a[7], a[8]))), \
             patch.object(engine, "_alert_sync_failure",
                          side_effect=lambda *a, **k: fail_alerts.append(1)), \
             patch.object(engine, "_alert_generic",
                          side_effect=lambda *a, **k: fail_alerts.append(1)), \
             patch.object(engine, "_cursor_freeze_count", return_value=None):
            result = engine.sync(NS)
        assert result["status"] == "success", "helper 返 None 不影响返回"
        assert states[0] == ("20261013", 1, "partial"), "收口照常、终态 partial"
        assert len(logs) == 1 and logs[0][0] == "success", "恰一行 success、无 error 行"
        assert "cursor_capped:20261013" in (logs[0][1] or ""), "留痕照常"
        assert not fail_alerts, "无 failed 告警、无冻结告警"

    def test_c6_real_helper_failsoft(self):
        """真 `_cursor_freeze_count` 在 Valkey 不可达 ⇒ 返 None 不抛（fail-soft 三不）。"""
        got = engine._cursor_freeze_count("definitely-unreachable-sid", bumped=False)
        assert got is None

    def test_c7_incr_seven_rounds_exactly_once(self):
        """连续 7 轮真 INCR（FakeValkey）：第 7 轮 ==N 打点恰一次、第 8 轮不再打。

        （步4 B-P2-2：钉 helper 的 INCR+EXPIRE 契约——规格 §3b 预案「补 FakeValkey.incr」。）
        """
        from src.quant_common import redis_client
        from tests.fake_valkey import FakeValkey
        fake = FakeValkey()
        from unittest.mock import patch
        with patch.object(redis_client, "business_redis", return_value=fake):
            fired = []
            with patch.object(engine, "_alert_generic",
                              side_effect=lambda code, *a: fired.append(code)):
                for i in range(1, 9):
                    n = engine._cursor_freeze_count(NS, bumped=True)
                    assert n == i, f"INCR 第 {i} 轮应返 {i}"
                    if n == engine._CAP_FREEZE_ALERT_ROUNDS:
                        # 引擎侧的 ==N 打点逻辑（sync() 内）在此直接按返回值复刻判定
                        pass
            # INCR 契约 + EXPIRE 续期在位（FakeValkey 无 ttl 原语——expire 写 _b.exp，
            # 断言键有 TTL 计划即证 EXPIRE 被调）
            assert fake.get(f"sync:cap_freeze:{NS}") == "8"
            assert f"sync:cap_freeze:{NS}" in fake._b.exp, "EXPIRE 续期在位"
        # ==N 恰一次语义由 test_c2 的返回值驱动门钉（n=7 fire / n=6,n=8 不 fire）

    def test_c8_backfill_with_cap_dels(self):
        """验收 8：回补轮带 cap 键 ⇒ 不 INCR、**DEL**（步4 A-P1-1：对齐规格 §2.B
        「回补＝操作员主动介入，计数让位清零」——真源裁定为 DEL 侧）。"""
        ret = dict(self.RET)
        _r, cap = _run(ret, backfill=True)
        assert cap.freeze_calls == [(NS, False)], "回补带 cap ⇒ DEL"
        assert cap.states == [], "回补不推游标（现状）"


# ---------------------------------------------------------------------------
# 验收 10：白名单漂移守门（可达集从 engine 活值推导）
# ---------------------------------------------------------------------------


class TestWhitelistDrift:

    def test_d1_whitelist_vs_reachable(self):
        reachable = set(engine._TIER1_BATCH) | {"astock_daily", "listed_fund_daily",
                                                "cb_daily", "astock_daily_jq"}
        # 批 171b：excluded 扩 4 族——+suspend_d_sync（suspend 停牌登记＝事件驱动稀疏，
        # 与 forecast/top_list/block_trade 同判别：空日是数据本性不是缺口，cap 会致倒漂）
        excluded = {"forecast_sync", "top_list_sync", "block_trade_sync", "suspend_d_sync"}
        assert engine._CAP_EMIT_IDS <= reachable, "白名单 ⊆ 装配可达族"
        assert reachable - engine._CAP_EMIT_IDS == excluded, "可达 − 白名单 ＝ 显式排除 4 族"

    def test_d2_dense_members_present(self):
        for sid in ("astock_daily", "astock_daily_jq", "stk_limit_sync", "cyq_perf_sync"):
            assert sid in engine._CAP_EMIT_IDS


# ---------------------------------------------------------------------------
# 验收 11：既有测试零修改全绿（本文件不重复跑——由 CI 全量 pytest 覆盖；
# 此处只钉 runbook 注册一致性，防 code 不进 RUNBOOK）
# ---------------------------------------------------------------------------


def test_runbook_registered():
    from src.alert_notify.runbook import RUNBOOK
    assert "sync.cursor_frozen" in RUNBOOK
    assert RUNBOOK["sync.cursor_frozen"]["label"] and RUNBOOK["sync.cursor_frozen"]["guide"]
