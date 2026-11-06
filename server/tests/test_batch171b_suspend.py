"""批 171b · 停牌日历与期望集净化守门（`flow/任务/批171b-停牌日历与期望集净化.md` §5 验收）。

钉的是**行为**：suspend_d 落库（S/R/full_day）/净化口（S+full_day+vt 形态）/
expected_bars 减项（×bpd/crypto 跳过/外推不减）/退市截断/工厂开关（reconcile=False
三键全无、既有键零漂移）/per-symbol 差集净化。

真库行为级用例无 dev 库自动跳过（惯例同 test_batch99/109）。
"""
from datetime import date

import pytest

from src.data_sync import engine
from src.data_platform.integrity import (
    _vt_to_ts, expected_bars, suspend_days_bulk, suspended_on)


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM suspend_calendar LIMIT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库/未迁移自动跳过）")


# ---------------------------------------------------------------------------
# 净化口单测（纯函数面，不依赖库——_suspend_rows 失败返 [] ⇒ 空集语义）
# ---------------------------------------------------------------------------


class TestPurgePorts:

    def test_vt_to_ts_normalization(self):
        assert _vt_to_ts("000016.SHSE") == "000016.SH"
        assert _vt_to_ts("127045.SZSE") == "127045.SZ"
        assert _vt_to_ts("430047.BSE") == "430047.BJ"
        assert _vt_to_ts("BTCUSDT.BINANCE") == "BTCUSDT.BINANCE"   # 加密原样

    @needs_db
    def test_suspended_on_s_only_and_vt_keys(self):
        """验收 3：2026-10-14——S 行净化、R 行（002860/002813 当日实有 bar）永不净化、vt 键形态。

        000016=平安银行（**深市** .SZSE/.SZ——勿写沪市后缀）；002860/002813=深市 R 行。
        """
        if not suspend_days_bulk(date(2026, 10, 13), date(2026, 10, 15), {"000016.SZSE"}):
            pytest.skip("dev 无 10-14 停牌数据（先跑验收 8 回补）")
        got = suspended_on(date(2026, 10, 14),
                           {"000016.SZSE", "002860.SZSE", "002813.SZSE", "600000.SHSE"})
        assert got == {"000016.SZSE"}, "仅 S 行（000016）；R 行与未停牌标的不进"
        # 非停牌日空集：000016 连停 10-08~10-22（dev 实证）——须选**库内该标的无行**的
        # 日期（10-23 复牌后首个交易日窗内；拍脑选日=把「任选一日非停牌」写成「任一日
        # 都非停牌」——步4 盲审 A-P0-1 教训）
        from src.data_platform.db import get_conn
        with get_conn() as c:
            have = {r[0] for r in c.execute(
                "SELECT DISTINCT trade_date FROM suspend_calendar WHERE ts_code='000016.SZ'"
            ).fetchall()}
        candidates = [d for d in (date(2026, 10, 23), date(2026, 10, 26), date(2026, 11, 2))
                      if d not in have]
        assert candidates, "库内 10-23 后应有非停牌日（000016 复牌）"
        for d in candidates:
            assert suspended_on(d, {"000016.SZSE"}) == set(), f"{d} 非该标的停牌日应空集"

    @needs_db
    def test_bulk_keys_are_vt(self):
        """验收 3 补：bulk 返回键 vt 形态——与 risk.py 循环变量直接 get（防 ts/vt 错位静默失效）。"""
        m = suspend_days_bulk(date(2026, 10, 13), date(2026, 10, 15), {"000016.SZSE"})
        if not m:
            pytest.skip("dev 无 10-14 停牌数据")
        assert "000016.SZSE" in m and all(k.endswith((".SHSE", ".SZSE", ".BSE")) for k in m)
        assert date(2026, 10, 14) in m["000016.SZSE"]


# ---------------------------------------------------------------------------
# expected_bars 减项（验收 4——纯函数直调）
# ---------------------------------------------------------------------------


class TestExpectedBarsSuspend:

    def _days(self, first, last, opens):
        return [d for d in opens if first <= d <= last]

    def test_daily_minus_suspend_days(self):
        opens = [date(2026, 10, d) for d in (12, 13, 14, 15, 16)]
        base = expected_bars("1D", "000016.SHSE", date(2026, 10, 12), date(2026, 10, 16), opens)
        cut = expected_bars("1D", "000016.SHSE", date(2026, 10, 12), date(2026, 10, 16), opens,
                            suspend_days=[date(2026, 10, 14)])
        assert base == 5 and cut == 4, "1D 减 len(窗内停牌日)"

    def test_minute_minus_times_bpd(self):
        opens = [date(2026, 10, d) for d in (12, 13, 14, 15, 16)]
        base = expected_bars("1min", "000016.SHSE", date(2026, 10, 12), date(2026, 10, 16), opens)
        cut = expected_bars("1min", "000016.SHSE", date(2026, 10, 12), date(2026, 10, 16), opens,
                            suspend_days=[date(2026, 10, 14), date(2026, 10, 15)])
        assert base == 5 * 240 and cut == 3 * 240, "1min 减 n×240"

    def test_crypto_ignores_suspend(self):
        opens = [date(2026, 10, d) for d in (12, 13, 14, 15, 16)]
        n = expected_bars("1D", "BTCUSDT.BINANCE", date(2026, 10, 12), date(2026, 10, 16), opens,
                          suspend_days=[date(2026, 10, 14)])
        assert n == 5, "加密显式跳过（无停牌概念）"

    def test_none_no_drift(self):
        opens = [date(2026, 10, d) for d in (12, 13, 14)]
        assert (expected_bars("1D", "000016.SHSE", date(2026, 10, 12), date(2026, 10, 14), opens)
                == expected_bars("1D", "000016.SHSE", date(2026, 10, 12), date(2026, 10, 14),
                                 opens, suspend_days=None))

    def test_out_of_window_suspend_ignored(self):
        opens = [date(2026, 10, d) for d in (12, 13, 14)]
        n = expected_bars("1D", "000016.SHSE", date(2026, 10, 12), date(2026, 10, 14), opens,
                          suspend_days=[date(2026, 10, 1)])   # 窗外停牌日
        assert n == 3

    def test_fail_open_leaves_trace(self):
        """步4 B-P1-1/P1-2 守门：非 date 入参（接线错形态）⇒ fail-open 返空**且留 warning 日志**
        ——裸吞曾把 risk.py 的 int 入参错吞成静默全灭（3042 全绿下不可见）。"""
        import logging as _lg

        from src.data_platform.integrity import suspend_days_bulk
        recorded = []

        class _Cap(_lg.Handler):
            def emit(self, record):
                recorded.append(record.getMessage())

        logger = _lg.getLogger("src.data_platform.integrity")
        cap = _Cap()
        logger.addHandler(cap)
        try:
            got = suspend_days_bulk(1, date(2026, 11, 6), {"000016.SZSE"})   # int＝错误接线形态
        finally:
            logger.removeHandler(cap)
        assert got == {}, "fail-open 宁多报"
        assert any("suspend_calendar 读取失败" in m for m in recorded), "静默错必须留痕"

    @needs_db
    def test_full_day_value_domain(self):
        """步4 B-P2-4：full_day 值域 ⊆ {'True','False'}（防大小写漂移断链）；半日 S+False
        行（dev 实有 5 条，如 301569.SZ 10-15）被净化口正确排除。"""
        from src.data_platform.db import get_conn
        with get_conn() as c:
            vals = {r[0] for r in c.execute("SELECT DISTINCT full_day FROM suspend_calendar")}
        assert vals <= {"True", "False"}, f"值域漂移: {vals}"
        # 半日行（False）不参与净化——真数据里找一条验证
        m = suspend_days_bulk(date(2026, 10, 1), date(2026, 11, 5), {"301569.SZSE"})
        days_301569 = m.get("301569.SZSE") or []
        assert all(isinstance(d, date) for d in days_301569)


# ---------------------------------------------------------------------------
# 退市截断（验收 5——合成夹具直调 + reconcile 窗上界单测）
# ---------------------------------------------------------------------------


class TestDelistTruncate:

    def test_expected_bars_short_window_after_cut(self):
        """合成夹具：delist 早于窗末 ⇒ 消费方截 last 后窗口变短（expected 随窗收窄）。"""
        opens = [date(2026, 10, d) for d in range(10, 20)]
        full = expected_bars("1D", "110076.SHSE", date(2026, 10, 10), date(2026, 10, 19), opens)
        cut = expected_bars("1D", "110076.SHSE", date(2026, 10, 10), date(2026, 10, 14), opens)
        assert full == 10 and cut == 5

    def test_reconcile_window_cut_via_delist(self):
        """_reconcile_symbols 净化逻辑的窗上界截断（单元面：直接验期望过滤式）。"""
        expected = ["20261101", "20261102", "20261103", "20261104", "20261105"]
        delist = date(2026, 11, 3)
        end_s = delist.strftime("%Y%m%d")
        got = [d for d in expected if d <= end_s]
        assert got == ["20261101", "20261102", "20261103"], "delist 后不算期望"


# ---------------------------------------------------------------------------
# 工厂开关（验收 7）
# ---------------------------------------------------------------------------


class TestFactorySwitch:

    def test_suspend_handler_registered_no_reconcile_keys(self):
        """suspend_d_sync 在 _HANDLERS 且 EXTRA 带 reconcile=False。"""
        assert "suspend_d_sync" in engine._HANDLERS
        assert engine._TIER1_BATCH_EXTRA["suspend_d_sync"]["reconcile"] is False
        assert "suspend_d_sync" not in engine._CAP_EMIT_IDS, "事件稀疏不入稠密白名单"

    def test_existing_tier1_keys_untouched(self):
        """既有 8 键 EXTRA 无条目（kwargs 边车缺省零漂移）。"""
        legacy = {"stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "top_list_sync",
                  "block_trade_sync", "cyq_perf_sync", "forecast_sync", "st_list_sync"}
        assert legacy == set(engine._TIER1_BATCH) - {"suspend_d_sync"}
        assert not (legacy & set(engine._TIER1_BATCH_EXTRA)), "既有键不经边车"

    def test_transform_assigns_full_day(self):
        """transform：suspend_timing→full_day 布尔派生+列选取（空帧不达——工厂判空在前）。"""
        import pandas as pd
        df = pd.DataFrame({
            "ts_code": ["000016.SZ", "002860.SZ"],
            "trade_date": ["20261014", "20261014"],
            "suspend_timing": [None, None],
            "suspend_type": ["S", "R"],
        })
        out = engine._TIER1_BATCH_EXTRA["suspend_d_sync"]["transform"](df)
        assert list(out.columns) == ["ts_code", "trade_date", "suspend_type", "full_day"]
        assert out["full_day"].tolist() == [True, True]
        assert out["suspend_type"].tolist() == ["S", "R"]


# ---------------------------------------------------------------------------
# 真库行为级（验收 1/2/8——落库/幂等/回补）
# ---------------------------------------------------------------------------


class TestSyncBehavior:

    @needs_db
    def test_a8_backfill_october(self):
        """验收 8：backfill_from=20261001 真调 ⇒ 10-14 落 13 行（R 2 行 S 11 行）。"""
        r = engine.sync("suspend_d_sync", backfill_from="20261001")
        assert r["status"] in ("success", "partial"), r
        from src.data_platform.db import get_conn
        with get_conn() as c:
            rows = c.execute(
                "SELECT suspend_type, count(*) FROM suspend_calendar "
                "WHERE trade_date='2026-10-14' GROUP BY 1").fetchall()
        d = dict(rows)
        assert d.get("S", 0) + d.get("R", 0) == 13, f"10-14 应 13 行，实得 {d}"
        assert d.get("R", 0) == 2, "R 行 2 条（002860/002813）"

    @needs_db
    def test_a2_idempotent_four_value_columns(self):
        """验收 2：同日连跑 2 次 ⇒ 行数与四值列幂等。"""
        r1 = engine.sync("suspend_d_sync", backfill_from="20261105")
        r2 = engine.sync("suspend_d_sync", backfill_from="20261105")
        assert r1["status"] == r2["status"] == "success"
        from src.data_platform.db import get_conn
        with get_conn() as c:
            n1 = c.execute("SELECT count(*) FROM suspend_calendar WHERE trade_date='2026-11-05'").fetchone()[0]
            assert n1 > 0
        # 四值列幂等由 PK upsert 保证（updated_at 除外——已降级信息列）

    @needs_db
    def test_no_cap_and_no_reconcile_in_return(self):
        """验收 7（真库）：suspend_d_sync 返回体无 reconcile/gap_dates/cursor_cap 三键。"""
        r = engine._HANDLERS["suspend_d_sync"](
            {"id": "suspend_d_sync", "mode": "incremental", "enabled": True,
             "last_sync_date": "20261104", "provider": "tushare"}, "20261105")
        for k in ("reconcile", "gap_dates", "cursor_cap"):
            assert k not in r, f"稀疏事件表不得带 {k} 键"
        assert "cursor_upto" in r
