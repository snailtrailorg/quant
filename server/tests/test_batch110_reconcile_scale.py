"""批 110（产出 D/E）：对账**读取规模契约**——真批量 ＋ 硬时限直抛 ＋ 体量上界。

任务＝`flow/任务/批110-对账判据可判定化.md` v3 产出 D（读取规模契约）与产出 E 的对应用例。

钉的是**成本形状**（不是「函数存在」）：

1. **批量读取等价**：`_local_dates_map`（scope 级一次）逐 symbol 必须等于单标的 `_local_dates`
   ——「省流」若改语义就是**静默丢数据**；`None` 纪律（fail-closed）两侧同款。
   `_repullable_map`（内存筛）必须与 SQL 版 `_list_repullable` 同判。
2. ⭐ **DB 往返上界＝常数**（与 scope 内 symbol 数**无关**）：这是「能跑在真库规模上」的结构
   保证——逐标的往返在 7 标的 × 多 scope 下会把周日窗口吃光（产出 D 的立项理由）。
3. **`_sync_gap_sync` 批量版 ≡ 逐条 `_sync_gap_sync_one`**：同一输入两路**落表行集一致**。
4. ⭐ **`SoftTimeLimitExceeded` 直抛不被吞**：软时限必须**中止整轮**（吞掉 ⇒ 任务「成功」实则
   半途而废、且判据会把未跑的 scope 当「跳过轮」）；**普通错误仍按 per-symbol 隔离**（对照）。
5. **体量超阈 ⇒ 响亮拒绝**（`_GAP_ROW_BUDGET`）：**不落主张** ＋ `sync.reconcile_budget` 告警
   （以告警替代静默；显名取舍＝用漏报换稳定）。

DB 依赖：真库行为级（无 dev 库自动跳过）；写侧只碰 `__t110s` 命名空间且用完即清。
载体说明：`_local_dates`/`_local_dates_map` 是**表无关**的通用读口（SQL 模板里没有表语义），
故用 `sync_gap` 作等价性载体，无需造 `bar_1d` 数据。
"""
from contextlib import ExitStack
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from src.data_sync import engine

NS = "__t110s"
SID = NS + "_binance_perp_daily"
S1, S2, S3 = f"{NS}_BTC.BINANCE", f"{NS}_ETH.BINANCE", f"{NS}_SOL.BINANCE"
SYMS = [S1, S2, S3]
SPEC = ("bar_daily", "perp", "1D", "bar_1d", "UTC", "perp")
_UNSET = object()


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def _exec(sql: str, params: tuple = ()) -> None:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()


def _q(sql: str, params: tuple = ()) -> list:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def _scalar(sql: str, params: tuple = ()):
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


def _rows(sid: str) -> list[tuple]:
    return [(r[0], str(r[1]), str(r[2]), r[3], r[4], r[5]) for r in _q(
        "SELECT symbol, gap_start, gap_end, state, hit_rounds, last_hit_round FROM sync_gap "
        "WHERE sync_id=%s ORDER BY symbol, gap_start", (sid,))]


def _wipe() -> None:
    _exec("DELETE FROM sync_gap WHERE LEFT(sync_id, %s) = %s", (len(NS), NS))


@pytest.fixture(autouse=True)
def _clean_ns():
    try:
        _wipe()
    except Exception:  # noqa: S110
        pass
    yield
    try:
        _wipe()
    except Exception:  # noqa: S110
        pass


def _now():
    return datetime.now(timezone.utc)


class _FakeCrypto:
    provider = "binance"
    venue = "BINANCE"

    def available_range(self, kind):
        return ("2019-12-31", None)

    def publish_lag(self, kind):
        return 0


def _seed_gap(sym: str, gstart: str, gend: str, *, state: str = "open",
              pull_count: int = 0, days_ago: int = 0) -> None:
    _exec(
        "INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state, pull_count, "
        "last_seen, hit_rounds, last_hit_round) "
        "VALUES (%s,%s,%s,%s,%s,%s, now() - (%s * interval '1 day'), 0, 0)",
        (SID, sym, gstart, gend, state, pull_count, days_ago))


def _run_scale(universe, *, round_id=11, repull=None, budget=None,
               today_end=date(2026, 10, 4), local_map=_UNSET):
    """跑一轮 `_reconcile_symbols`：**默认保留真 IO**（`_local_dates_map`/`_sync_gap_scope_rows`/
    `_sync_gap_sync` 都真走库）——只 patch 外部依赖（adapter / SM / 时钟 / 告警）。

    `local_map` 给定 ⇒ patch 批量读口（用于**不关心**读取路径的用例，避免依赖 `bar_1d` 现状）。
    返回 `(out, alerts)`。
    """
    alerts: list[tuple] = []
    ad = _FakeCrypto()
    uni = [(v[0], v[1], (v[2] if len(v) > 2 else None)) for v in universe]

    def _ok_repull(*_a, **_k):
        return 1

    with ExitStack() as st:
        st.enter_context(patch.object(engine, "_RECONCILE_SYMBOL_SCOPE", {SID: SPEC}))
        st.enter_context(patch.object(
            engine, "_get_config",
            return_value={"id": SID, "enabled": True, "provider": "binance"}))
        st.enter_context(patch.object(engine, "_get_supply_adapter", return_value=ad))
        st.enter_context(patch.object(engine, "_sm_universe", return_value=uni))
        st.enter_context(patch.object(engine, "_crypto_end", return_value=today_end))
        st.enter_context(patch.object(engine, "_trade_dates_in_range", return_value=None))
        st.enter_context(patch.object(engine, "_next_round_id", return_value=round_id))
        st.enter_context(patch.object(engine, "_bump_cover_low", return_value=None))
        st.enter_context(patch.object(
            engine, "_repull_symbol", side_effect=repull if repull is not None else _ok_repull))
        st.enter_context(patch.object(
            engine, "_alert_generic",
            side_effect=lambda code, title, body: alerts.append((code, title, body))))
        st.enter_context(patch.object(engine, "_alert_sync_gaps"))
        if budget is not None:
            st.enter_context(patch.object(engine, "_GAP_ROW_BUDGET", budget))
        if local_map is not _UNSET:
            st.enter_context(patch.object(engine, "_local_dates_map", return_value=local_map))
        out = engine._reconcile_symbols()
    return out, alerts


# ---------------------------------------------------------------------------
# 1：批量读取 ≡ 单标的读取（语义等价是「省流」的合法性前提）
# ---------------------------------------------------------------------------


class TestBatchEquivalence:
    @needs_db
    def test_local_dates_map_equals_single(self):
        _seed_gap(S1, "2026-10-02", "2026-10-02")
        _seed_gap(S1, "2026-10-05", "2026-10-05")
        _seed_gap(S2, "2026-10-03", "2026-10-03")
        mp = engine._local_dates_map("sync_gap", "gap_start::text", SYMS)
        assert mp is not None
        assert mp[S1] == {"2026-10-02", "2026-10-05"}, "证明真读到（防「两端都空」的假等价）"
        for sym in SYMS:
            single = engine._local_dates("sync_gap", "gap_start::text", "symbol=%s", (sym,))
            assert mp.get(sym, set()) == (single or set()), sym

    @needs_db
    def test_local_dates_map_empty_symbols(self):
        assert engine._local_dates_map("sync_gap", "gap_start::text", []) == {}

    @needs_db
    def test_local_dates_map_none_on_bad_table(self):
        """fail-closed 纪律两侧同款：不可读 ⇒ `None`（**不得**退化成空集 ⇒ 全窗口假缺口）。"""
        assert engine._local_dates_map("no_such_table_110s", "gap_start::text", SYMS) is None
        assert engine._local_dates("no_such_table_110s", "gap_start::text",
                                   "symbol=%s", (S1,)) is None

    @needs_db
    def test_repullable_map_equals_sql_version(self):
        _seed_gap(S1, "2026-09-01", "2026-09-01", pull_count=0, days_ago=2)    # 候选
        _seed_gap(S1, "2026-09-10", "2026-09-10", pull_count=99, days_ago=2)   # 超 _GAP_REPULL_MAX
        _seed_gap(S2, "2026-09-02", "2026-09-02", pull_count=0, days_ago=0)    # 太新
        _seed_gap(S3, "2026-09-03", "2026-09-03", state="closed", days_ago=2)  # 非 open
        prev = engine._sync_gap_scope_rows(SID, SYMS)
        mp = engine._repullable_map(prev)
        for sym in SYMS:
            assert mp.get(sym, []) == engine._list_repullable(SID, sym), sym
        assert mp[S1] == [("2026-09-01", "2026-09-01")], "只留限频内候选"


# ---------------------------------------------------------------------------
# 2：⭐ DB 往返上界 ＝ 常数（与 scope 内 symbol 数无关）
# ---------------------------------------------------------------------------


class TestRoundTripBound:
    """结构保证：所有库操作都是 **scope 级**（两读 ＋ 一写 ＋ 判据读）——symbol 数不影响。"""

    def _count_conns(self, n_sym: int) -> int:
        calls: list[int] = []
        real = engine.get_conn

        def counting(*a, **k):
            calls.append(1)
            return real(*a, **k)

        uni = [(f"{NS}_S{i}.BINANCE", _now(), "2026-10-01") for i in range(n_sym)]
        with patch.object(engine, "get_conn", side_effect=counting):
            _run_scale(uni)
        return len(calls)

    @needs_db
    def test_conn_count_flat_in_scope_size(self):
        c1 = self._count_conns(1)
        c8 = self._count_conns(8)
        assert c1 == c8, f"往返数必须恒定（scope 级），实测 1 标的={c1} vs 8 标的={c8}"
        assert 1 <= c1 <= 8, f"常数上界（本路径：两读＋一写＋判据读），实测 {c1}"

    @needs_db
    def test_conn_count_stable_across_runs(self):
        assert self._count_conns(3) == self._count_conns(3)


# ---------------------------------------------------------------------------
# 3：批量写 ≡ 逐条写（同输入 ⇒ 同落表行集）
# ---------------------------------------------------------------------------


class TestBatchWriteEquivalence:
    @needs_db
    def test_batch_equals_per_symbol(self):
        st_batch = engine._sync_gap_sync(
            SID, symbols=[S1, S2],
            win_map={S1: ("20261001", "20261004"), S2: ("20261001", "20261004")},
            round_id=None,                       # 与 `_sync_gap_sync_one` 同口径（不做判据）
            gaps_map={S1: [("20261002", "20261002")], S2: []},
            uncertain_map={}, pulled_map={})
        assert st_batch["opened"] == 1 and st_batch["closed"] == 0
        batch_rows = _rows(SID)
        _wipe()
        engine._sync_gap_sync_one(SID, S1, win_lo="20261001", win_hi="20261004",
                                  gaps=[("20261002", "20261002")])
        engine._sync_gap_sync_one(SID, S2, win_lo="20261001", win_hi="20261004", gaps=[])
        assert _rows(SID) == batch_rows, "批量与逐条必须落同一批行"

    @needs_db
    def test_win_endpoint_normalized(self):
        """窗口端点**一律 `_anchor` 归一**：给紧凑 `20260927` 也必须落成 `2026-09-27`
        （否则「既有行」与「新锚」键空间不等 ⇒ 假 closed ＋ 假 open）。"""
        engine._sync_gap_sync(
            SID, symbols=[S1], win_map={S1: ("20260927", "20261004")}, round_id=None,
            gaps_map={}, uncertain_map={S1: "inception_unknown"}, pulled_map={})
        assert _rows(SID) == [(S1, "2026-09-27", "2026-10-04", "uncertain", 0, 0)]


# ---------------------------------------------------------------------------
# 4：⭐ 硬时限直抛（vs 普通错误隔离）
# ---------------------------------------------------------------------------


class TestSoftTimeLimit:
    @needs_db
    def test_soft_time_limit_propagates(self):
        exc = pytest.importorskip("celery.exceptions").SoftTimeLimitExceeded
        _seed_gap(S1, "2026-09-01", "2026-09-01", pull_count=0, days_ago=2)  # ⇒ 有重拉候选
        with pytest.raises(exc):
            _run_scale([(S1, _now(), "2026-10-01")], local_map={S1: set()}, repull=exc())

    @needs_db
    def test_ordinary_error_is_isolated(self):
        """对照：普通异常**按 per-symbol 隔离**（计 `errors`，不中止整轮）。"""
        _seed_gap(S1, "2026-09-01", "2026-09-01", pull_count=0, days_ago=2)
        out, _a = _run_scale([(S1, _now(), "2026-10-01")],
                             local_map={S1: set()}, repull=ValueError("boom"))
        assert out[SID]["errors"] >= 1


# ---------------------------------------------------------------------------
# 5：体量超阈 ⇒ 响亮拒绝（不主张 ＋ 告警）
# ---------------------------------------------------------------------------


class TestBudget:
    @needs_db
    def test_over_budget_claims_nothing_and_alerts(self):
        out, alerts = _run_scale(
            [(S1, _now(), "2026-10-01")], local_map={S1: set()},
            budget={"bar_daily": 0})          # 任一段数 > 0 即超阈
        assert out[SID]["budget_exceeded"] == 1
        assert [c for c, _t, _b in alerts if c == "sync.reconcile_budget"]
        assert _scalar("SELECT count(*) FROM sync_gap WHERE sync_id=%s", (SID,)) == 0, \
            "超阈 ⇒ **不落主张**（以告警替代静默）"

    @needs_db
    def test_under_budget_does_claim(self):
        out, alerts = _run_scale([(S1, _now(), "2026-10-01")], local_map={S1: set()},
                                 budget={"bar_daily": 10 ** 6})
        assert out[SID]["budget_exceeded"] == 0
        assert not [c for c, _t, _b in alerts if c == "sync.reconcile_budget"]
        assert _scalar("SELECT count(*) FROM sync_gap WHERE sync_id=%s", (SID,)) == 1


# ---------------------------------------------------------------------------
# 6：⭐ 抑制轮不许过门（P1-1）＋ 整 scope 不可读告警（P1-4）
# ---------------------------------------------------------------------------


class TestGateUnderSuppression:
    @needs_db
    def test_budget_exceeded_suppresses_gate(self):
        """budget 超阈轮：行仍 `open` 且保留旧 `hit_rounds` ⇒ 必须显式归零，否则**假过门**。"""
        uni = [(S1, _now(), "2026-10-01")]
        out = None
        for rd in range(11, 11 + engine._GAP_JUDGE_ROUNDS):
            out, _a = _run_scale(uni, round_id=rd, local_map={S1: set()})
        assert out[SID]["hit_ge_n"] == 1, "前置：已达阈值"
        out2, _a = _run_scale(uni, round_id=11 + engine._GAP_JUDGE_ROUNDS,
                              local_map={S1: set()}, budget={"bar_daily": 0})
        assert out2[SID]["budget_exceeded"] == 1
        assert out2[SID]["hit_ge_n"] == 0, "超阈轮不主张 ⇒ 不构成「命中」"
        assert out2[SID]["judged"] is False and out2[SID]["suppressed"] is True


class TestLocalUnreadableAlert:
    @needs_db
    def test_scope_unreadable_alerts(self):
        """整 scope 不可读 ⇒ 全族转 uncertain，**必须**响亮（不再只留 journal 一行）。"""
        out, alerts = _run_scale([(S1, _now(), "2026-10-01")], local_map=None)
        assert [c for c, _t, _b in alerts if c == "sync.local_unreadable"]
        assert out[SID]["uncertain"] == 1, "告警不改变 §5.4 的 uncertain 语义"
        assert _scalar("SELECT count(*) FROM sync_gap WHERE sync_id=%s", (SID,)) == 1
