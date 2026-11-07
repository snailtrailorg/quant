"""批 171d-C · 稠密族游标派生化守门（`flow/任务/批171d-C-游标派生化.md` v2 §1/§3）。

钉**行为级**（171 教训——路径不可分辨反面）：sync() 读侧派生收口（min 生效/空域 None
不收口/查询失败留痕原值/timeout 归入失败/越窗数据不推游标/收口+failed 词不变/
:3762 tier1 失败轮 end_date 不变）+ crypto 跨度三钉。双盲审 A-P0-2（读侧单源）、
B-P0-1（:3762 门翻转）、A-P1-1（空域静默）裁定全量入钉。
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.data_sync import engine


NS = "test171dc"


class _Cap:
    def __init__(self):
        self.states = []   # (date, saved, term)
        self.logs = []     # (status, error/note)
        self.freeze_calls = []


def _run(handler_ret, *, cfg=None, local_max=None, sid=NS):
    """跑 engine.sync() 全路径；local_max=mock _local_max 返回值（None=不收口口令）。"""
    cap = _Cap()
    fake_cfg = cfg or {"id": sid, "name": "x", "mode": "incremental", "enabled": True,
                       "last_sync_date": "20261010", "last_sync_ts": None, "last_status": "idle"}

    class _FakeLock:
        acquired = True

        def __init__(self, sid):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
         patch.object(engine, "_get_config", return_value=fake_cfg), \
         patch.object(engine, "_HANDLERS", {sid: MagicMock(return_value=handler_ret)}), \
         patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
         patch.object(engine, "_local_max", return_value=local_max), \
         patch.object(engine, "_mark_running"), \
         patch.object(engine, "_update_sync_state",
                      side_effect=lambda s, d, n, t: cap.states.append((str(d), n, t))), \
         patch.object(engine, "_log",
                      side_effect=lambda *a, **k: cap.logs.append((a[7], a[8]))), \
         patch.object(engine, "_alert_generic"), \
         patch.object(engine, "_alert_sync_failure"):
        result = engine.sync(sid)
    return result, cap


class TestDerivedClamp:
    """验收 1（sync() 读侧派生收口——A-P0-2 单源链）。"""

    def test_min_clamps_advance(self):
        """推进 20261107 > 本地末日 20261104 ⇒ 收口 20261104、词 partial（idle→partial 通则）。
        171 收官件：note 落 `derived_clamp:<mx>` 词汇（审计面——A-P2-5）。"""
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261105", "failed_dates": [],
                        "expected_days": 3, "actual_days": 3,
                        "cursor_upto": "20261107"},
                       local_max="20261104", sid="astock_daily")
        assert cap.states[0][0] == "20261104", "收口至数据末日"
        assert cap.states[0][2] == "partial", "收口生效 ⇒ 词 partial"
        assert any("derived_clamp:20261104" in (e or "") for _s, e in cap.logs), \
            "sync_log note 留痕（禁静默）"

    def test_empty_domain_none_no_clamp(self):
        """空域 None（bar_5min 现状 0 行）⇒ 不收口（防 None 传染 NULL 全窗循环）。"""
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261105", "failed_dates": [],
                        "expected_days": 3, "actual_days": 3,
                        "cursor_upto": "20261107"},
                       local_max=None, sid="astock_minute_5min")
        assert cap.states[0][0] == "20261107", "None ⇒ 原推进值"
        assert cap.states[0][2] == "idle"

    def test_derived_lower_not_raised(self):
        """只紧不松：max > 推进值 ⇒ 不抬高（min 结构单向）。"""
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261105",
                        "failed_dates": ["20261106:X"],
                        "last_success_date": "20261105"},
                       local_max="20261107", sid="astock_daily")
        assert cap.states[0][0] == "20261105", "部分失败推连续成功末——不因 max 抬高"

    def test_failed_word_unchanged_when_clamped(self):
        """收口+failed 共现 ⇒ 词仍 partial、游标收口（status 单源不改写——P1-4）。

        步4 A-P1-3 修正：部分失败（lsd 非 None）+local_max<lsd ⇒ 游标=_mx 且词 partial
        （原用例误走全失败 fallback 分支+双活断言＝路径不可分辨反面教材）。
        """
        _r, cap = _run({"pulled": 3, "saved": 3, "start": "20261105",
                        "failed_dates": ["20261106:X"],
                        "last_success_date": "20261106"},
                       local_max="20261104", sid="astock_daily")
        assert cap.states[0][0] == "20261104", "部分失败轮派生收口仍生效（恒收）"
        assert cap.states[0][2] == "partial", "收口+failed 共现词=partial（不被改写）"

    def test_non_derrieved_family_untouched(self):
        """非派生族（如 astock_basic）⇒ 真函数在注册表 miss 处返 None 不查库（零成本
        直证——步4 A-P2-4：原 tripwire 被内层 patch 遮蔽＝空钉，改用真函数+断言值）。"""
        assert engine._local_max("astock_basic") is None, "注册表 miss ⇒ None（不查库）"
        _r, cap = _run({"pulled": 1, "saved": 1, "start": "20261105",
                        "failed_dates": [], "last_success_date": "20261107"},
                       local_max=None, sid="astock_basic")
        assert cap.states[0][0] == "20261107", "原推进（无收口链）"

    def test_none_advance_path_skips_derived(self):
        """A 增审新 P1：last_date=None 入参路径 ⇒ 派生整体跳过（None 语义 :302 原样——
        异常轮首跑「游标不动只退避」承诺不被 min(None, max) 前跳破坏）。读侧实现下
        异常路径（:598）不经过判定链 ⇒ 本用例钉「全失败 fallback 同样不进派生」。"""
        _r, cap = _run({"pulled": 0, "saved": 0, "start": "20261105",
                        "failed_dates": ["20261105:X"],
                        "last_success_date": None},
                       local_max="20261104", sid="astock_daily")
        # 全失败 fallback：写回 cfg 旧值 20261010（非 20261104——派生不得介入 None/fallback）
        assert cap.states[0][0] == "20261010", "fallback 旧值原样（派生不前跳不回退）"
        assert cap.states[0][2] == "failed"


class TestLocalMaxGuard:
    """验收 4（查询失败留痕）+ timeout 归入失败（B-P1 三护栏）。"""

    def test_query_failure_fail_open_with_warning(self, monkeypatch, caplog):
        """_local_max 抛错路径 ⇒ 返 None + warning（fail-open 不阻同步）。"""
        def _boom(sql):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(engine, "get_conn", _boom)
        got = engine._local_max("stk_limit_sync")
        assert got is None
        assert any("派生收口本地 max 读取失败" in r.message for r in caplog.records), \
            "查询失败必须留痕（171 纪律）"

    def test_empty_domain_silent(self, monkeypatch, caplog):
        """空域（fetchone 返 (None,)）⇒ 返 None 且**零 warning**（A-P1-1）。"""

        class _Cur:
            def fetchone(self):
                return (None,)

        class _Conn:
            def execute(self, *a, **k):
                return _Cur()

        class _Ctx:
            def __enter__(self):
                return _Conn()

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(engine, "get_conn", lambda: _Ctx())
        with caplog.at_level("WARNING"):
            got = engine._local_max("astock_minute_5min")
        assert got is None
        assert not any("读取失败" in r.message for r in caplog.records), "空域不告警"

    def test_non_derived_returns_none_without_query(self):
        assert engine._local_max("binance_perp_daily") is None, "加密族暂缓——不查"
        assert engine._local_max("suspend_d_sync") is None, "稀疏族不在派生集"

    def test_derived_sql_registry_complete(self):
        """12 族 SQL 声明全覆盖（漂移即红）。"""
        assert set(engine._DERIVED_MAX_SQL) == set(engine._DERIVED_CURSOR_IDS)


class TestTier1SparseGateFlip:
    """B-P0-1/:3762——清空 _CAP_EMIT_IDS 后 tier1 稠密族不落稀疏闸。"""

    def _tier1_run(self, monkeypatch, sid):
        from datetime import date as _d
        monkeypatch.setattr(engine, "_read_sync_kind", lambda s: {
            "kind": "stk_limit", "sub_kind": None, "pg_table": "stk_limit",
            "pk_cols": "{trade_date,ts_code}", "float_cols": "{}",
            "text_cols": "{}", "rebuild": "incremental"})
        monkeypatch.setattr(engine, "_family_start",
                            lambda *a, **k: (_d(2014, 1, 1), []))
        monkeypatch.setattr(engine, "_data_ready_end_date", lambda lag=0: "20261106")

        class _FD:
            empty = True

        class _FA:
            provider = "tushare"
            venue = ""

            def fetch_supply(self, *a, **k):
                return _FD()

        monkeypatch.setattr(engine, "_get_supply_adapter", lambda cfg: _FA())
        monkeypatch.setattr(engine, "_sync_gap_sync_one", lambda *a, **k: {})
        monkeypatch.setattr(engine, "_local_dates", lambda *a, **k: None)
        monkeypatch.setattr(engine, "_trade_dates_in_range",
                            lambda lo, hi: ["20261105", "20261106"])
        monkeypatch.setattr(engine, "_alert_sync_gaps", lambda *a: None)
        cfg = {"id": sid, "mode": "incremental", "enabled": True,
               "last_sync_date": "20261104", "provider": "tushare", "_max_span_days": 366}
        # 触发失败日：fetch_supply 抛错 ⇒ failed_dates 非空
        def _fail_fetch(*a, **k):
            raise RuntimeError("upstream down")
        monkeypatch.setattr(_FA, "fetch_supply", _fail_fetch)
        return engine._HANDLERS[sid](cfg, "20261106", backfill_from="20261105")

    def test_tier1_dense_failure_round_keeps_end(self, monkeypatch):
        """tier1 稠密族失败轮 cursor_upto==end_date（不落稀疏闸＝D1 借尸还魂防）。"""
        r = self._tier1_run(monkeypatch, "stk_limit_sync")
        assert r["cursor_upto"] == "20261106", "派生族失败轮 end_date 推进（缺口走 sync_gap 主张）"

    def test_tier1_not_in_derived_still_sparse_gated(self, monkeypatch):
        """对照：稀疏族（forecast）失败轮仍走稀疏成功闸（停首失败日前）。"""
        r = self._tier1_run(monkeypatch, "forecast_sync")
        assert r["cursor_upto"] == "20261104", "min(failed)-1（稀疏闸保持）"


class TestCryptoClamp:
    """验收 5（crypto 跨度三钉）。"""

    def _crypto_handler_run(self, monkeypatch, span_days, backfill_from):
        from datetime import datetime, timedelta, timezone

        class _Win:
            start = date(2024, 1, 1)
            end = date(2026, 11, 6)

        class _Adapter:
            provider = "binance"
            venue = "BINANCE"

            def account_window(self):
                return _Win()

            def list_symbols(self):
                return ["BTCUSDT"]

            def publish_lag(self, kind):
                return 1

            def fetch_supply(self, *a, **k):
                import pandas as pd
                return pd.DataFrame()

            def to_bar_rows(self, df, freq):
                return []

        monkeypatch.setattr(engine, "_get_supply_adapter", lambda cfg: _Adapter())
        monkeypatch.setattr(engine, "_window_floors", lambda *a, **k: (None, []))
        # 源界 None ⇒ _family_start 走 fallback 30d；直接钉 family floor
        monkeypatch.setattr(engine, "_family_start",
                            lambda *a, **k: (date(2024, 1, 1), []))
        monkeypatch.setattr(engine, "_sm_upsert_crypto", lambda *a: None)
        import src.data_platform.db as _db
        monkeypatch.setattr(_db, "save_bars_overwrite", lambda *a: 0)
        monkeypatch.setattr(_db, "save_bars", lambda *a: 0)
        handler = engine._make_crypto_bar_handler(
            "binance_perp_daily", "bar_daily", "perp", "1D",
            label="binance", enum_hint="x")
        cfg = {"id": "binance_perp_daily", "mode": "incremental", "enabled": True,
               "last_sync_date": None, "provider": "binance",
               "_max_span_days": span_days, "retention": None}
        return handler(cfg, "20261106", backfill_from=backfill_from)

    def test_span_clamped_no_next_from_drift(self, monkeypatch):
        """回补窗 2024-01-01~2026-11-06（>366 天）⇒ clamped.span=True + span_end=start+365
        + next_from=span_end+1（续跑点良定义——A-P2-3 对称补齐）。"""
        r = self._crypto_handler_run(monkeypatch, 366, "20240101")
        assert r["clamped"]["span"] is True
        assert r["clamped"]["span_end"] == "20241231", "start+366-1（含两端）"
        assert r["next_from"] == "20250101", "续跑点=span_end+1"

    def test_short_window_no_clamp(self, monkeypatch):
        """短窗（<366）⇒ 无 clamped 键（零漂移）。"""
        r = self._crypto_handler_run(monkeypatch, 366, "20260101")
        assert "clamped" not in r


class TestCapRetirementZeroTouch:
    """验收 3（D1 零触碰——反向钉）。"""

    def test_cap_emit_ids_empty(self):
        assert engine._CAP_EMIT_IDS == frozenset()

    def test_cap_functions_gone(self):
        """171 收官件：三函数已删（重定义即红——反向守门在 test_batch171a）。"""
        assert not hasattr(engine, "_cursor_cap")
        assert not hasattr(engine, "_cursor_cap_healed")


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM sync_config LIMIT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


class TestLocalMaxRealSQL:
    """步4 A-P1-2：12 条派生 SQL **真库逐条执行**守门——P0（index_daily 时区缺失）正是
    从「全 mock 零执行」的洞漏掉（161b/171「改查询必须看真实输出」纪律）。

    断言口径：返回 None（空域——bar_5min 现状合法）或 8 位 YYYYMMDD；**SH 渲染一致性**
    ——bar 族与独立基准查询（同表同谓词 max() 聚合+AT TIME ZONE）值必须相等（防
    时区/渲染形态回归——index_daily 曾 utc/sh 差一天）。
    """

    @needs_db
    def test_all_twelve_sqls_execute_and_render_sh(self):
        import re
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            for sid, (_tbl, sql) in sorted(engine._DERIVED_MAX_SQL.items()):
                got = engine._local_max(sid)
                assert got is None or re.fullmatch(r"\d{8}", got), \
                    f"{sid}: 返回非 YYYYMMDD/None 形态: {got!r}"
                # SH 渲染一致性（B 增审 P2 修正）：**bar_* 表族按表名无条件比对**——禁用
                # 「SQL 内含 AT TIME ZONE」作门（index_daily P0 正是删掉该子句的形态——
                # 门控会静默跳过＝守门自失效）。基准自身已 SH 锚定，被测 SQL 无论渲染
                # 形态如何，LIMIT1 值偏差即红。tier1 五族（trade_date 文本列无时区语义）
                # 跳过基准比对（形态断言已足）。
                tbl = _tbl
                if not tbl.startswith("bar_"):
                    continue
                if "security_master" in sql:      # bar 族谓词形态
                    cat = re.search(r"category='(\w+)'", sql).group(1)
                    base = conn.execute(
                        f"SELECT to_char(max(ts AT TIME ZONE 'Asia/Shanghai'),'YYYYMMDD') "
                        f"FROM {tbl} WHERE symbol IN "
                        f"(SELECT vt_symbol FROM security_master WHERE category=%s)",
                        (cat,)).fetchone()[0]
                else:                              # bar_index 全表
                    base = conn.execute(
                        f"SELECT to_char(max(ts AT TIME ZONE 'Asia/Shanghai'),'YYYYMMDD') "
                        f"FROM {tbl}").fetchone()[0]
                assert got == (str(base) if base else None), \
                    f"{sid}: LIMIT1={got} 与 SH 基准聚合={base} 不一致（时区渲染回归）"
