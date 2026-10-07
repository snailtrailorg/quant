"""批 110：对账**判据地基**——连续命中计数 ＋ 断档复位 ＋ inception 收编 ＋ 互证闸 ＋ 可观测。

任务＝`flow/任务/批110-对账判据可判定化.md` v3；上位设计 `flow/方案/同步窗口与参数分层-设计.md`
§八.5（删除门控＝可观测判据）· §5.4（不确定抑制）· §九.1（快照新鲜度）· §十 G①（inception 可读点＝SM）。

钉的是**行为**（不是「函数存在」）：

1. **连续命中计数**：新开 ⇒ `hit_rounds=1`；对齐 ⇒ 累加（`last_hit_round == round_id-1` 时）。
2. ⭐ **断档复位**（批 110 核心）——「中间整轮跳过」后计数**归 1**，判据**不过门**：
   反例（若不复位）：轮 1–4 命中 → 轮 5 整轮跳过 → 轮 6–7 命中 ⇒ `hr=6` 假过门，实际连续仅 2 轮。
3. **误报证据**（R2，**P-2 已删**）：P-1 同轮振荡 / P-3 抑制失效；**第四类判不了**（诚实边界）。
4. **inception 收编**（裁定 G①）：下界取口＝`sm_inception`（SM 单一真源）——
   SM 有 `list_date` ⇒ 正常主张；**有 symbol 但 `list_date` 空** ⇒ 结构性未知（`uncertain`）。
5. **「SM 无 symbol」＝ scope 级**（遍历源就是 SM 本身，per-symbol 级结构上不可达）⇒ **响亮告警**。
6. **互证闸**：`_RECONCILE_UNKNOWN_INCEPTION` 声明 vs 实测「有 symbol 且 `list_date` 空」**双向比对**；
   **只在快照新鲜时评估**（stale ⇒ `unverified`，不判）。
7. **覆盖率长期低**（V7）⇒ 响亮告警（防「一次刷新抖动写 None 未恢复」致缺口永久静默抑制）。
8. **可观测摘要**（产出 C）——每 scope 一行 ＋ 每轮总计，含 `round_id`/`N`/覆盖率。

DB 依赖：真库行为级（无 dev 库自动跳过）；写侧只碰 `__t110j` 命名空间且用完即清。
"""
import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.data_sync import engine

NS = "__t110j"
SID = NS + "_binance_perp_daily"


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def _q(sql: str, params: tuple = ()) -> list:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def _exec(sql: str, params: tuple = ()) -> None:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()


def _hit(sid: str) -> list[tuple]:
    """该 scope 的 (symbol, gap_start, hit_rounds, last_hit_round)。"""
    return [(r[0], str(r[1]), r[2], r[3]) for r in _q(
        "SELECT symbol, gap_start, hit_rounds, last_hit_round FROM sync_gap "
        "WHERE sync_id=%s ORDER BY symbol, gap_start", (sid,))]


def _states(sid: str) -> list[tuple]:
    return [(str(r[0]), r[1], r[2]) for r in _q(
        "SELECT gap_start, state, reason FROM sync_gap WHERE sync_id=%s "
        "ORDER BY symbol, gap_start", (sid,))]


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


class _FakeCrypto:
    """crypto adapter 桩（`_window_floors` 只剩 `available_range`/`publish_lag` 由它提供——
    inception 已收编为 SM 读口，见 `sm_inception`）。"""

    provider = "binance"
    venue = "BINANCE"

    def __init__(self, src_lo="2019-12-31", lag=0):
        self._src_lo, self._lag = src_lo, lag

    def available_range(self, kind):
        return (self._src_lo, None)

    def publish_lag(self, kind):
        return self._lag

    def fetch_supply(self, kind, sub_kind, *, symbol, start, end, freq):
        import pandas as pd
        return pd.DataFrame([{"ts": start}])

    def to_bar_rows(self, df, freq):
        return [("X", 1, 2, 3, 4, 5, 6)]


def _now():
    return datetime.now(timezone.utc)


def _run(adapter, *, universe, local, round_id, today_end=date(2026, 10, 4),
         scope=None, repull_error=None, cover_streak=None, cfg=None):
    """跑一轮 `_reconcile_symbols`（scope 收窄成单条；DB 侧只碰 `__t110j`）。

    `universe` ＝ `[(vt_symbol, updated_at, list_date)]`（`list_date` 是 SM 的 inception 真值）。
    `round_id` ＝ patch `_next_round_id` 的返回（Valkey 不可用 ⇒ 传 `None`）。
    `cover_streak` ＝ patch `_bump_cover_low` 的返回（`None` ＝ 模拟 Valkey 不可用）。
    """
    cfg = cfg if cfg is not None else {"id": SID, "enabled": True, "provider": "binance"}
    scope = scope if scope is not None else {
        SID: ("bar_daily", "perp", "1D", "bar_1d", "UTC", "perp")}
    uni = [(v[0], v[1], (v[2] if len(v) > 2 else None)) for v in universe]
    local_map = None if local is None else {v[0]: set(local) for v in uni}
    alerts: list[tuple] = []

    def _fake_repull(ad, kind, sub_kind, freq, src_sym, g0, g1):
        return 1

    with patch.object(engine, "_RECONCILE_SYMBOL_SCOPE", scope), \
         patch.object(engine, "_get_config", return_value=cfg), \
         patch.object(engine, "_get_supply_adapter", return_value=adapter), \
         patch.object(engine, "_sm_universe", return_value=uni), \
         patch.object(engine, "_crypto_end", return_value=today_end), \
         patch.object(engine, "_local_dates_map", side_effect=lambda *a, **k: local_map), \
         patch.object(engine, "_trade_dates_in_range", return_value=None), \
         patch.object(engine, "_repull_symbol", side_effect=repull_error or _fake_repull), \
         patch.object(engine, "_next_round_id", return_value=round_id), \
         patch.object(engine, "_bump_cover_low", return_value=cover_streak), \
         patch.object(engine, "_alert_generic",
                      side_effect=lambda code, title, body: alerts.append((code, title, body))), \
         patch.object(engine, "_alert_sync_gaps"):
        out = engine._reconcile_symbols()
    return out, alerts


LOCAL_FULL = {"20261001", "20261002", "20261003", "20261004"}   # 无缺
LOCAL_MISS_02 = {"20261001", "20261003", "20261004"}            # 缺 10-02


# ---------------------------------------------------------------------------
# 1：连续命中计数（三态）＋ ⭐ 断档复位
# ---------------------------------------------------------------------------


class TestRoundStreak:
    @needs_db
    def test_first_open_sets_hit_round_1(self):
        out, _a = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                       local=LOCAL_MISS_02, round_id=11)
        assert out[SID]["new_open"] == 1
        assert _hit(SID) == [("BTC.BINANCE", "2026-10-02", 1, 11)]

    @needs_db
    def test_aligned_round_increments_hit(self):
        ad = _FakeCrypto()
        for rd in (11, 12):
            _run(ad, universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                 local=LOCAL_MISS_02, round_id=rd)
        assert _hit(SID) == [("BTC.BINANCE", "2026-10-02", 2, 12)], "同一缺口续存 ⇒ 累加"

    @needs_db
    def test_closed_row_keeps_history(self):
        """`closed` 保留 `hit_rounds/last_hit_round`（判据**读史**；不得清零）。"""
        ad = _FakeCrypto()
        _run(ad, universe=[("BTC.BINANCE", _now(), "2026-10-01")],
             local=LOCAL_MISS_02, round_id=11)
        _run(ad, universe=[("BTC.BINANCE", _now(), "2026-10-01")],
             local=LOCAL_FULL, round_id=12)            # 补齐 ⇒ closed
        assert _states(SID)[0][1] == "closed"
        assert _hit(SID) == [("BTC.BINANCE", "2026-10-02", 1, 11)], "closed 后保留史"

    @needs_db
    def test_skipped_round_then_hit_resets_to_1(self):
        """⭐ **断档复位**：轮 11–14 命中 → 轮 15 **整轮跳过**（行不动）→ 轮 16 命中 ⇒ **归 1**。

        若不复位（只判 `last_hit_round == 本轮`），轮 16 会看到 `hr=5`（假连续）⇒ **假过门**。
        """
        ad = _FakeCrypto()
        for rd in (11, 12, 13, 14):
            _run(ad, universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                 local=LOCAL_MISS_02, round_id=rd)
        assert _hit(SID)[0][2] == 4
        # 轮 15：该 scope 整轮未跑（scope 表为空）——「中断」而非「连续」
        _run(ad, universe=[], local=set(), round_id=15, scope={})
        assert _hit(SID)[0][2] == 4, "跳过轮不动行（计数既不增也不归零）"
        # 轮 16：命中，但上一命中是 14 ≠ 15 ⇒ 复位为 1
        _run(ad, universe=[("BTC.BINANCE", _now(), "2026-10-01")],
             local=LOCAL_MISS_02, round_id=16)
        assert _hit(SID) == [("BTC.BINANCE", "2026-10-02", 1, 16)]

    @needs_db
    def test_hit_ge_n_at_threshold(self):
        """连续 N 轮（`_GAP_JUDGE_ROUNDS`）⇒ 判据**过门**（`hit_ge_n == 1`）。"""
        ad = _FakeCrypto()
        out = None
        for rd in range(11, 11 + engine._GAP_JUDGE_ROUNDS):
            out, _a = _run(ad, universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                           local=LOCAL_MISS_02, round_id=rd)
        assert _hit(SID)[0][2] == engine._GAP_JUDGE_ROUNDS
        assert out[SID]["hit_ge_n"] == 1
        assert out[SID]["judged"] is True

    @needs_db
    def test_no_round_id_means_not_judged(self):
        """Valkey 不可用（`round_id=None`）⇒ **只观测不判定**：不维护命中列、`hit_ge_n=0`。"""
        out, _a = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                       local=LOCAL_MISS_02, round_id=None)
        assert out[SID]["judged"] is False and out[SID]["hit_ge_n"] == 0
        assert _hit(SID) == [("BTC.BINANCE", "2026-10-02", 0, 0)], "不判定 ⇒ 不维护命中列"


# ---------------------------------------------------------------------------
# 2：误报证据（R2；P-2 已删）
# ---------------------------------------------------------------------------


class TestFalseEvidence:
    def test_p1_same_round_oscillation(self):
        """P-1：同一锚本轮既 `opened` 又 `closed`（自相矛盾）。

        ⭐ **形态回归**：三集同用 `(symbol, anchor)`——`opened` 若取**裸锚**则 `&` **恒空**
        ⇒ 本检测静默失效（批 110 自查实证）。
        """
        st = {"new": [("BTC", "2026-10-02", "2026-10-02")],
              "closed_anchors": [("BTC", "2026-10-02")], "uncertain_anchors": []}
        j = engine._gap_judge(st, [], 11)
        assert j["false_evidence"]["same_round_oscillation"] == [("BTC", "2026-10-02")]

    def test_p3_suppress_leak(self):
        """P-3：`uncertain` 的本轮仍产出 `open`/`new`（代码不应到达）。"""
        st = {"new": [("BTC", "2026-10-02", "2026-10-02")],
              "closed_anchors": [], "uncertain_anchors": [("BTC", "2026-10-02")]}
        j = engine._gap_judge(st, [], 11)
        assert j["false_evidence"]["suppress_leak"] == [("BTC", "2026-10-02")]

    def test_different_symbols_do_not_intersect(self):
        """**跨标的不得误判**：A 开 B 闭 ⇒ 无振荡（形态同但 symbol 不同 ⇒ 交集空）。"""
        st = {"new": [("A", "2026-10-02", "2026-10-02")],
              "closed_anchors": [("B", "2026-10-02")], "uncertain_anchors": []}
        j = engine._gap_judge(st, [], 11)
        assert j["false_evidence"]["same_round_oscillation"] == []

    def test_clean_round_has_no_evidence(self):
        st = {"new": [("BTC", "2026-10-02", "2026-10-02")],
              "closed_anchors": [("BTC", "2026-10-05")], "uncertain_anchors": []}
        j = engine._gap_judge(st, [], 11)
        assert j["false_evidence"]["same_round_oscillation"] == []
        assert j["false_evidence"]["suppress_leak"] == []

    def test_hit_ge_n_counts_open_only(self):
        rows = [("A", "2026-10-02", "open", 4, 11), ("B", "2026-10-02", "closed", 9, 11)]
        j = engine._gap_judge({"new": [], "closed_anchors": [], "uncertain_anchors": []}, rows, 11)
        assert j["hit_ge_n"] == 1, "只数 `open`（closed 是史，不是当前缺口）"
        assert len(j["by_anchor"]) == 2


# ---------------------------------------------------------------------------
# 3：inception 收编（裁定 G①）与「SM 缺两类分开」
# ---------------------------------------------------------------------------


class TestInceptionInclusion:
    @needs_db
    def test_sm_list_date_enables_claims(self):
        """SM **有** `list_date` ⇒ 期望集自它起算、正常主张缺口（不再恒 `uncertain`）。"""
        out, _a = _run(_FakeCrypto(), universe=[("BTC-USDT-SWAP.OKX", _now(), "2026-10-01")],
                       local=LOCAL_MISS_02, round_id=11)
        assert out[SID]["new_open"] == 1 and out[SID]["unknown_inception"] == 0

    @needs_db
    def test_null_list_date_is_structural_unknown(self):
        """「SM **有** symbol 但 `list_date` 空」＝ **结构性未知** ⇒ `uncertain(inception_unknown)`。"""
        out, _a = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), None)],
                       local=LOCAL_MISS_02, round_id=11)
        assert out[SID]["unknown_inception"] == 1 and out[SID]["new_open"] == 0
        assert _states(SID)[0][2] == "inception_unknown"
        assert _hit(SID)[0][2] == 0, "不确定态不参与连续判据"

    @needs_db
    def test_shared_adapter_inception_is_not_read(self):
        """读口是 **SM**，不是 adapter 活体：adapter 报值而 SM 空 ⇒ 仍 `uncertain`。"""
        ad = _FakeCrypto()
        ad.symbol_inception = lambda s: "2026-10-01"      # 活体有值——但**不该被读**
        out, _a = _run(ad, universe=[("BTC.BINANCE", _now(), None)],
                       local=LOCAL_MISS_02, round_id=11)
        assert out[SID]["unknown_inception"] == 1, "活体值不得冒充 SM（单一真源）"

    @needs_db
    def test_sm_missing_symbol_is_scope_level_alert(self):
        """「SM **无**该族 symbol」＝ scope 级快照不完整 ⇒ **响亮告警**（不静默跳过）。"""
        out, alerts = _run(_FakeCrypto(), universe=[], local=set(), round_id=11)
        assert out[SID] == {"skipped": "SM 无该族标的"}
        assert any(c == "sync.universe_missing" for c, _t, _b in alerts)

    def test_universe_source_is_sm_itself(self):
        """结构断言：遍历源＝`_sm_universe` ⇒ per-symbol 级「SM 无 symbol」**结构上不可达**
        （故该告警**只能是 scope 级**——V6）。"""
        src = open(engine.__file__, encoding="utf-8").read()
        assert "for vt_symbol, _upd, ld in sm_rows" in src
        assert "sm_rows = _sm_universe(" in src


# ---------------------------------------------------------------------------
# 4：互证闸（V5，**双向**比对；只在新鲜时评估）
# ---------------------------------------------------------------------------


class TestCrossCheckGate:
    @needs_db
    def test_consistent_no_alert(self):
        """SID 不在声明集 ＋ list_date 全非空 ⇒ 一致 ⇒ 不告警。"""
        _o, alerts = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                          local=LOCAL_FULL, round_id=11)
        assert not [c for c, _t, _b in alerts if c == "sync.inception_cover_low"]

    @needs_db
    def test_measured_empty_but_not_declared_alerts(self):
        """实测「有 symbol 且 `list_date` 空」而**未声明** ⇒ 漂移 ⇒ 告警。"""
        _o, alerts = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), None)],
                          local=LOCAL_FULL, round_id=11)
        assert [c for c, _t, _b in alerts if c == "sync.inception_cover_low"]

    @needs_db
    def test_declared_but_measured_has_dates_alerts(self):
        """**反向**：声明「结构性不可得」但实测有值 ⇒ 声明过时 ⇒ 告警（双向互证）。"""
        with patch.object(engine, "_RECONCILE_UNKNOWN_INCEPTION", frozenset({SID})):
            _o, alerts = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                              local=LOCAL_FULL, round_id=11)
        assert [c for c, _t, _b in alerts if c == "sync.inception_cover_low"]

    @needs_db
    def test_stale_snapshot_skips_gate(self):
        """**stale ⇒ `unverified`，不判**——防把「瞬时探不到」误判为**结构性未知**（V5）。"""
        old = _now() - timedelta(days=engine._SNAPSHOT_STALE_DAYS + 1)
        _o, alerts = _run(_FakeCrypto(), universe=[("BTC.BINANCE", old, None)],
                          local=LOCAL_FULL, round_id=11)
        assert not [c for c, _t, _b in alerts if c == "sync.inception_cover_low"]


# ---------------------------------------------------------------------------
# 5：覆盖率长期低（V7）
# ---------------------------------------------------------------------------


class TestCoverLowAlert:
    @needs_db
    def test_streak_at_threshold_alerts(self):
        """连续 ≥ `_SM_COVER_STALE_ROUNDS` 轮覆盖率不足 ⇒ 响亮告警。

        声明集补上 SID ⇒ 互证闸（V5）判**一致**，隔离出**覆盖率**路径——否则 V5 会先以
        同一 code 报警，用例就测不到 V7（两者**共用** `sync.inception_cover_low`）。
        """
        with patch.object(engine, "_RECONCILE_UNKNOWN_INCEPTION", frozenset({SID})):
            _o, alerts = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), None)],
                              local=LOCAL_FULL, round_id=11,
                              cover_streak=engine._SM_COVER_STALE_ROUNDS)
        assert [c for c, _t, _b in alerts if c == "sync.inception_cover_low"]

    @needs_db
    def test_below_threshold_no_alert(self):
        with patch.object(engine, "_RECONCILE_UNKNOWN_INCEPTION", frozenset({SID})):
            _o, alerts = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), None)],
                              local=LOCAL_FULL, round_id=11,
                              cover_streak=engine._SM_COVER_STALE_ROUNDS - 1)
        assert not [c for c, _t, _b in alerts if c == "sync.inception_cover_low"]

    @needs_db
    def test_full_cover_no_alert(self):
        _o, alerts = _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                          local=LOCAL_FULL, round_id=11,
                          cover_streak=engine._SM_COVER_STALE_ROUNDS + 5)
        assert not [c for c, _t, _b in alerts if c == "sync.inception_cover_low"]


# ---------------------------------------------------------------------------
# 6：可观测摘要（产出 C）——判据的证据面
# ---------------------------------------------------------------------------


class TestSummary:
    @needs_db
    def test_scope_and_round_summary_logged(self, caplog):
        with caplog.at_level(logging.INFO, logger="data_sync"):
            _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                 local=LOCAL_MISS_02, round_id=11)
        txt = caplog.text
        assert f"gap_reconcile sync_id={SID}" in txt, txt
        assert "round_id=11" in txt
        assert f"N={engine._GAP_JUDGE_ROUNDS}" in txt, "N 必须进摘要（否则判据不可复核）"
        assert "sm_covered=1/1" in txt
        assert "gap_reconcile_done" in txt, "每轮总计行"

    @needs_db
    def test_summary_marks_coverage_gap(self, caplog):
        with caplog.at_level(logging.INFO, logger="data_sync"):
            _run(_FakeCrypto(), universe=[("BTC.BINANCE", _now(), None)], local=LOCAL_FULL,
                 round_id=11)
        assert "sm_covered=0/1" in caplog.text
        assert "unknown_inception=1" in caplog.text


# ---------------------------------------------------------------------------
# 8：⭐ 门控合取（P1-1，步 4 盲审必修）——本轮主张被抑制 ⇒ 不许过门
# ---------------------------------------------------------------------------


class TestGateConjunction:
    def test_suppressed_blocks_pass(self):
        rows = [("A", "2026-10-02", "open", 9, 11)]
        st = {"new": [], "closed_anchors": [], "uncertain_anchors": []}
        j = engine._gap_judge(st, rows, 11, suppressed=True)
        assert j["hit_ge_n"] == 0 and j["judged"] is False and j["suppressed"] is True

    def test_not_suppressed_passes(self):
        rows = [("A", "2026-10-02", "open", 9, 11)]
        st = {"new": [], "closed_anchors": [], "uncertain_anchors": []}
        j = engine._gap_judge(st, rows, 11)
        assert j["hit_ge_n"] == 1 and j["judged"] is True and j["suppressed"] is False

    @needs_db
    def test_coverage_below_one_suppresses_gate(self):
        """覆盖率 <1（同 scope 有 `list_date` 空标的）⇒ 该轮**不许过门**（方案 §产出B）。

        反证要点：抑制轮里**行仍 `open` 且 `hit_rounds` 保留旧值**（≥N）——不显式归零就会
        「拿旧值过门」＝**假过门**。
        """
        ad = _FakeCrypto()
        out = None
        for rd in range(11, 11 + engine._GAP_JUDGE_ROUNDS):
            out, _a = _run(ad, universe=[("BTC.BINANCE", _now(), "2026-10-01")],
                           local=LOCAL_MISS_02, round_id=rd)
        assert out[SID]["hit_ge_n"] == 1, "前置：已达阈值"
        out2, _a = _run(ad, universe=[("BTC.BINANCE", _now(), "2026-10-01"),
                                      ("ETH.BINANCE", _now(), None)],
                        local=LOCAL_MISS_02, round_id=11 + engine._GAP_JUDGE_ROUNDS)
        assert out2[SID]["sm_covered"] == 1 and out2[SID]["sm_total"] == 2
        assert out2[SID]["hit_ge_n"] == 0, "抑制轮拿旧 hit_rounds 过门 ＝ 假过门"
        assert out2[SID]["judged"] is False and out2[SID]["suppressed"] is True


# ---------------------------------------------------------------------------
# 9：⭐ 覆盖率计数断档复位（P1-5，步 4 盲审必修）——与 `hit_rounds` 同构
# ---------------------------------------------------------------------------


class _FakeValkey:
    def __init__(self):
        self.d: dict = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = str(v)

    def delete(self, k):
        self.d.pop(k, None)


class TestCoverLowStreakReset:
    def test_reset_across_unevaluated_round(self):
        """⭐ 该 scope **整轮未评估**（本函数不被调用）后 ⇒ 下次评估**归 1**，不得跨断档累加。

        反例（原实现：只靠「不低时清零」）：轮 11–12 低 → 轮 13 未评估（冻结）→ 轮 14 低
        ⇒ 旧实现给 3（跨过断档），阈值 2 ⇒ **假告警**。
        """
        fake = _FakeValkey()
        with patch("src.quant_common.redis_client.business_redis", return_value=fake):
            assert engine._bump_cover_low("s1", True, 11) == 1
            assert engine._bump_cover_low("s1", True, 12) == 2
            # 轮 13：该 scope 未被评估（未配置/已禁用 或 V6 无 symbol ⇒ 调用点 `continue`）
            assert engine._bump_cover_low("s1", True, 14) == 1, "跨过未评估轮 ⇒ 复位（不是 3）"

    def test_not_low_clears(self):
        fake = _FakeValkey()
        with patch("src.quant_common.redis_client.business_redis", return_value=fake):
            assert engine._bump_cover_low("s1", True, 11) == 1
            assert engine._bump_cover_low("s1", False, 12) == 0, "覆盖正常 ⇒ 清零"
            assert engine._bump_cover_low("s1", True, 13) == 1, "清零后再低 ⇒ 从 1 起"

    def test_no_round_id_not_maintained(self):
        fake = _FakeValkey()
        with patch("src.quant_common.redis_client.business_redis", return_value=fake):
            assert engine._bump_cover_low("s1", True, None) is None, "round_id 不可用 ⇒ 不维护"
        assert fake.d == {}, "不维护 ⇒ 不落键（失效方向＝少报，不误报）"


# ---------------------------------------------------------------------------
# 7：常量契约（判据参数落点）
# ---------------------------------------------------------------------------


class TestConstants:
    def test_judge_rounds_and_declared_set(self):
        assert engine._GAP_JUDGE_ROUNDS == 4, "R3 定值：周频 4 轮 ≈ 1 个自然月"
        assert "binance_perp_daily" in engine._RECONCILE_UNKNOWN_INCEPTION
        assert "okx_perp_daily" not in engine._RECONCILE_UNKNOWN_INCEPTION, \
            "OKX 有 instruments.listTime ⇒ 不该声明为结构性不可得"
        for sid in engine._RECONCILE_UNKNOWN_INCEPTION:
            assert sid in engine._RECONCILE_SYMBOL_SCOPE, "声明集必须是 scope 表的子集"

    def test_budget_is_per_family(self):
        """体量上界**按族**（daily 与 minute 量级差大；单值必致一族恒漏报/另一族无保护）。"""
        assert set(engine._GAP_ROW_BUDGET) >= {"bar_daily", "bar_minute"}
        assert engine._GAP_ROW_BUDGET["bar_minute"] > engine._GAP_ROW_BUDGET["bar_daily"]
