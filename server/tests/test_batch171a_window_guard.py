"""批 171a · D2 窗口化状态迁移守门（`flow/任务/批171a-D2-窗口化状态迁移.md` §5 验收）。

钉的是**行为**，不是实现细节：

1. `window_guard=True` ⇒ 窗口外既有行**零写入**（不 closed / 不 touch / 不转 uncertain /
   不对齐 / 不 pull / 不因新锚而 INSERT 重开）；
2. 窗口内语义**逐字不变**（该 closed 仍 closed）；
3. `window_guard=False`（默认）⇒ **逐字现状**，per-symbol stale 全族抑制门不受影响；
4. 新锚落在窗口外**非终态**行 `[gap_start, gap_end]` 内 ⇒ 仅从 INSERT/reopen 候选剔除；
5. 空窗 ⇒ `ValueError`（不静默退化）；R6 删除后窗外行 `gap_end` 不被对齐。

DB 依赖：真库行为级（无 dev 库自动跳过，惯例同 `test_batch99` / `test_batch109`）。
**写侧只碰 `__t171a` 命名空间且用完即清**——`sync_gap` 是派生视图（可清空重算），测试不污染。
"""
import pytest

from src.data_sync import engine

NS = "__t171a"          # 命名空间前缀（LEFT(sync_id, len(NS)) 精确匹配）


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


def _wipe() -> None:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM sync_gap WHERE LEFT(sync_id, %s) = %s", (len(NS), NS))
        conn.commit()


@pytest.fixture(autouse=True)
def _clean_ns():
    try:
        _wipe()
    except Exception:      # 无库时 needs_db 已接管，此处静默  # noqa: S110
        pass
    yield
    try:
        _wipe()
    except Exception:      # noqa: S110
        pass


def _seed(sid: str, gap_start: str, gap_end: str, state: str = "open",
          reason: str | None = None, symbol: str = "TEST") -> None:
    """经写侧唯一入口 `_upsert_sync_gap` 造一行（不裸 INSERT，避免绕过写侧纪律）。"""
    engine._upsert_sync_gap([(sid, symbol, gap_start, gap_end, state, reason)])


def _rows(sid: str) -> list[tuple]:
    """全列快照（symbol, gap_start, gap_end, state, reason, pull_count, last_seen, closed_at）。

    DATE/TIMESTAMP 列统一转字符串——psycopg 读回是 `date`/`datetime` 对象，与断言里的
    `YYYY-MM-DD` 字面量口径不一致（批 171a 编码期实测 KeyError）。
    """
    out = []
    for r in _q(
            "SELECT symbol, gap_start, gap_end, state, reason, pull_count, last_seen, closed_at "
            "FROM sync_gap WHERE sync_id=%s ORDER BY symbol, gap_start", (sid,)):
        rr = list(r)
        for i in (1, 2, 6, 7):
            rr[i] = str(rr[i]) if rr[i] is not None else None
        out.append(tuple(rr))
    return out


# ---------------------------------------------------------------------------
# 验收 1：D2 主判据 —— 10-14 的 open 行不再被本轮（10-15 单日窗）遗忘
# ---------------------------------------------------------------------------


class TestWindowGuardKeepsOuterRows:

    @needs_db
    def test_window_out_row_not_closed(self):
        sid = NS + "_v1"
        _seed(sid, "2026-10-14", "2026-10-14")
        before = _rows(sid)
        st = engine._sync_gap_sync_one(sid, "TEST", win_lo="2026-10-15", win_hi="2026-10-15",
                                       gaps=[])
        after = _rows(sid)
        assert after[0][3] == "open", "窗外 open 行不得被本轮 closed"
        assert after[0][6] == before[0][6], "last_seen 未变（未 touch）"
        assert after[0][7] is None, "closed_at 未写"
        assert after == before, "所有列逐字不变（零写入）"
        assert st["closed"] == 0 and st["closed_anchors"] == []

    @needs_db
    def test_window_in_row_still_closed(self):
        """验收 2：独立重置 fixture；窗内锚仍 closed（证明不是「一律不关」）。"""
        sid = NS + "_v2"
        _seed(sid, "2026-10-14", "2026-10-14")
        st = engine._sync_gap_sync_one(sid, "TEST", win_lo="2026-10-14", win_hi="2026-10-14",
                                       gaps=[])
        assert _rows(sid)[0][3] == "closed"
        assert st["closed"] == 1

    @needs_db
    def test_default_false_is_verbatim_current_behavior(self):
        """默认 `window_guard=False` 与改动前逐字一致：同 fixture 窗外行仍被 closed。

        与 `test_window_out_row_not_closed` 构成对照——同一输入，仅开关不同。
        """
        sid = NS + "_v3"
        _seed(sid, "2026-10-14", "2026-10-14")
        st = engine._sync_gap_sync(
            sid, symbols=["TEST"], win_map={"TEST": ("2026-10-15", "2026-10-15")},
            round_id=None, gaps_map={"TEST": []}, uncertain_map={}, pulled_map={})
        assert st["closed"] == 1
        assert _rows(sid)[0][3] == "closed"


# ---------------------------------------------------------------------------
# 验收 4 / R5：跨窗口不造重复行
# ---------------------------------------------------------------------------


class TestCrossWindowNoDuplicate:

    @needs_db
    def test_new_anchor_inside_outer_span_not_inserted(self):
        sid = NS + "_v4"
        _seed(sid, "2026-10-10", "2026-10-20")      # 窗外行，区间跨过本轮窗口
        before = _rows(sid)
        st = engine._sync_gap_sync_one(sid, "TEST", win_lo="2026-10-15", win_hi="2026-10-15",
                                       gaps=[("2026-10-15", "2026-10-15")])
        after = _rows(sid)
        assert len(after) == len(before) == 1, "不得 INSERT 新行（含 scope 级）"
        assert after == before, "原行所有列不变（含 gap_end ⇒ R6 删除后不扩展）"
        assert st["opened"] == 0 and st["updated"] == 0 and st["closed"] == 0


# ---------------------------------------------------------------------------
# 验收 5 / R1+R2：uncertain / touch 同样夹取，且不得凭空造行
# ---------------------------------------------------------------------------


class TestUncertainTouchClamped:

    @needs_db
    def test_uncertain_clamped_and_no_new_scope_row(self):
        sid = NS + "_v5"
        _seed(sid, "2026-10-14", "2026-10-14")
        before = _rows(sid)
        st = engine._sync_gap_sync_one(sid, "TEST", win_lo="2026-10-15", win_hi="2026-10-15",
                                       uncertain_reason="x")
        after = _rows(sid)
        assert len(after) == 1, "**不得**新插 scope 级 uncertain 行（钉 if live: 未被改 live_in:）"
        assert after == before, "窗外行状态/所有列不变"
        assert st["uncertain"] == 0

    @needs_db
    def test_touch_clamped_when_gaps_none(self):
        """`gaps=None` ⇒ `gaps_map=None` ⇒ 只刷窗内 last_seen；窗外行不动。"""
        sid = NS + "_v5b"
        _seed(sid, "2026-10-14", "2026-10-14")
        before = _rows(sid)
        engine._sync_gap_sync_one(sid, "TEST", win_lo="2026-10-15", win_hi="2026-10-15")
        assert _rows(sid) == before


# ---------------------------------------------------------------------------
# 验收 6：per-symbol stale 全族抑制门（window_guard=False）不受影响
# ---------------------------------------------------------------------------


class TestStaleFamilyNotClamped:

    @needs_db
    def test_false_writes_all_live_rows_including_outer_anchor(self):
        sid = NS + "_v6"
        _seed(sid, "2026-10-14", "2026-10-14")
        _seed(sid, "2026-10-15", "2026-10-15")
        st = engine._sync_gap_sync(
            sid, symbols=["TEST"], win_map={"TEST": ("2026-10-15", "2026-10-15")},
            round_id=None, gaps_map=None, uncertain_map={"TEST": "stale_source"},
            pulled_map={})                       # window_guard 默认 False
        rows = _rows(sid)
        assert len(rows) == 2
        assert [r[3] for r in rows] == ["uncertain", "uncertain"], "全族 live 行（含窗外锚）均转"
        assert st["uncertain"] == 2, "stat['uncertain'] 与库内实态一致（2490 硬编码 sm_total 同口径）"


# ---------------------------------------------------------------------------
# 验收 7：窗口内 fixture 在 False/True 下统计各键相等
# ---------------------------------------------------------------------------


class TestStatsSelfCheck:

    @needs_db
    def test_window_in_stats_equal_both_guards(self):
        def _run(guard: bool):
            sid = NS + "_v7"
            _wipe()                              # 两次独立重置 fixture
            _seed(sid, "2026-10-15", "2026-10-15")
            st = engine._sync_gap_sync(
                sid, symbols=["TEST"], win_map={"TEST": ("2026-10-15", "2026-10-15")},
                round_id=None, gaps_map={"TEST": []}, uncertain_map={}, pulled_map={},
                window_guard=guard)
            return st, _rows(sid)

        st_false, rows_false = _run(False)
        st_true, rows_true = _run(True)
        assert st_false == st_true, "窗口内语义逐字不变 ⇒ stats 各键相等"
        # 只比稳定列：last_seen/closed_at 是 now()，两次独立 fixture 必然不同
        assert [r[:6] for r in rows_false] == [r[:6] for r in rows_true]


# ---------------------------------------------------------------------------
# §3 前置 + §7 评审要点 ④：空窗 ValueError；不静默退化
# ---------------------------------------------------------------------------


class TestEmptyWindow:

    @needs_db
    def test_empty_window_raises_only_when_guard_true(self):
        sid = NS + "_v8"
        with pytest.raises(ValueError, match="window_guard 需要非空窗口"):
            engine._sync_gap_sync(
                sid, symbols=["TEST"], win_map={"TEST": ("", "")},
                round_id=None, gaps_map={"TEST": []}, window_guard=True)
        # 默认 False：空窗不报错（逐字现状）
        engine._sync_gap_sync(
            sid, symbols=["TEST"], win_map={"TEST": ("", "")},
            round_id=None, gaps_map={"TEST": []}, window_guard=False)


# ---------------------------------------------------------------------------
# R3 / R6 / R7：窗外行不对齐、gap_end 不变、pull 仅窗内锚计数
# ---------------------------------------------------------------------------


class TestAlignAndPullClamped:

    @needs_db
    def test_outer_row_not_aligned_and_pull_only_inner(self):
        sid = NS + "_v9"
        _seed(sid, "2026-10-14", "2026-10-15")
        _seed(sid, "2026-10-15", "2026-10-15")
        before = {r[1]: r for r in _rows(sid)}
        st = engine._sync_gap_sync(
            sid, symbols=["TEST"], win_map={"TEST": ("2026-10-15", "2026-10-15")},
            round_id=None,
            gaps_map={"TEST": [("2026-10-14", "2026-10-15"),
                               ("2026-10-15", "2026-10-15")]},
            uncertain_map={},
            pulled_map={"TEST": ["2026-10-14", "2026-10-15"]},
            window_guard=True)
        after = {r[1]: r for r in _rows(sid)}
        assert st["updated"] == 1 and st["closed"] == 0, "仅窗内行对齐；窗外行零写入"
        assert after["2026-10-14"][2] == before["2026-10-14"][2] == "2026-10-15", \
            "窗外行 gap_end 不被对齐（R6 删除后不做单向扩展）"
        assert after["2026-10-15"][5] == before["2026-10-15"][5] + 1, "窗内锚计 pull"
        assert after["2026-10-14"][5] == before["2026-10-14"][5], "窗外锚不计 pull"


# ---------------------------------------------------------------------------
# 步4 双盲审 P2 收口：混合 fixture 对照 / 窗内正向 / 倒置窗知情取舍
# ---------------------------------------------------------------------------


class TestReviewP2Hardening:

    @needs_db
    def test_mixed_fixture_guard_strictly_reduces_writes(self):
        """验收 7 证明力补强：窗内+窗外混合，True 下 closed 严格小于 False。

        顺带钉住 `test_batch110`「批量 ≡ 逐条」等价性用例的**巧合绿**：该用例是 fresh 库
        且锚均在窗内，两路（False / True）才恰好一致；窗外既有行时两路本应不等价
        （by design），不是无条件等价契约。
        """
        def _run(guard: bool):
            sid = NS + "_p2mix"
            _wipe()
            _seed(sid, "2026-10-14", "2026-10-14")      # 窗外
            _seed(sid, "2026-10-15", "2026-10-15")      # 窗内
            st = engine._sync_gap_sync(
                sid, symbols=["TEST"], win_map={"TEST": ("2026-10-15", "2026-10-15")},
                round_id=None, gaps_map={"TEST": []}, uncertain_map={}, pulled_map={},
                window_guard=guard)
            return st, _rows(sid)

        st_false, rows_false = _run(False)
        st_true, rows_true = _run(True)
        assert st_false["closed"] == 2, "False：两行都 closed（逐字现状）"
        assert st_true["closed"] == 1, "True：仅窗内行 closed，窗外行零写入"
        assert st_true["closed"] < st_false["closed"]
        assert [r[3] for r in rows_false] == ["closed", "closed"]
        assert [r[3] for r in rows_true] == ["open", "closed"]

    @needs_db
    def test_window_in_uncertain_and_touch_positive_path(self):
        """R1/R2 正向路径：窗内 live 行确实转 uncertain / 确实刷 last_seen。"""
        sid = NS + "_p2unc"
        _seed(sid, "2026-10-15", "2026-10-15")
        st = engine._sync_gap_sync_one(sid, "TEST", win_lo="2026-10-14", win_hi="2026-10-16",
                                       uncertain_reason="x")
        assert _rows(sid)[0][3] == "uncertain"
        assert st["uncertain"] == 1
        # touch 正向：gaps=None 时窗内行 last_seen 被刷新（两次独立事务 now() 必不同）
        sid2 = NS + "_p2touch"
        _seed(sid2, "2026-10-15", "2026-10-15")
        before = _rows(sid2)[0][6]
        engine._sync_gap_sync_one(sid2, "TEST", win_lo="2026-10-14", win_hi="2026-10-16")
        assert _rows(sid2)[0][6] != before, "窗内行 last_seen 已被 touch（未被误夹取）"

    @needs_db
    def test_inverted_window_writes_nothing(self):
        """知情取舍（设计 §6）：倒置窗 lo>hi ⇒ live_in 恒空 ⇒ 全族零写入、不插 scope 行。

        生产 `_trade_dates_in_range` 在倒置窗返回 None ⇒ `calendar_uncovered` 带倒置窗
        调进 per-date 路径；本批选择「不落表」而非「全族转 uncertain」或 fail-fast
        （后者会把日历信号变成同步失败）。钉成可证伪事实，防止将来无声改变。
        """
        sid = NS + "_p2inv"
        _seed(sid, "2026-10-15", "2026-10-15")
        before = _rows(sid)
        st = engine._sync_gap_sync_one(sid, "TEST", win_lo="2026-10-15", win_hi="2026-10-14",
                                       uncertain_reason="calendar_uncovered")
        assert _rows(sid) == before, "零写入（含不插 scope 级 uncertain 行）"
        assert st["uncertain"] == 0
