"""批 83b：四条收编 beat 的行为级钉（真库往返 + 调度口令等价）。

收编的验收判据是"**行为等价**"（任务书 83b 验收 2），故本文件钉三件：
1. **真库往返**——handler 真的把行写进 `static_symbols` / `convertible_terms`
   （只把**网络边**打桩：tushare 调用返回假 DataFrame；DB 写路径全真跑。
   这是"mock 绿"的反面：mock 只允许盖住无法在 CI 调用的外部 API，不允许盖住被测逻辑）。
   池内族（`pool_data` / `pool_data_full_calibrate`，0119 收编）的真库往返在
   `test_pool_data_collected.py`（10 表 + 第二源指纹，打桩面同样只盖外部源）。
2. **返回契约**——`sync()` 吃的结果字典键齐（pulled/saved/start/failed_dates/
   expected_days/actual_days）——缺键会被 sync() 当 0 处理并推进游标（数据洞）。
3. **调度口令等价**——新 sync_config 行的 cron 串与原 beat 的 crontab **逐字（或周期）一致**，
   且 `trade_day_filter='none'`（原 beat 无交易日过滤）——填错=行为变化。
   ↓ 四条 beat 的等价对照表唯一住本文件（其他文件不再重复断言 cron 串）
"""
import os
from unittest.mock import patch

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


_RESULT_KEYS = {"pulled", "saved", "start", "failed_dates", "expected_days", "actual_days"}
# sync() 已退役 _sync_by_trade_date 系才需要 last_success_date；本两键不返回（无条件推进游标，
# 与收编前 beat 无游标语义一致——原 beat 根本不碰 sync_config 游标）。


class TestStaticSymbolsHandler:
    """`_sync_static_list`（原 static-list-sync beat）。

    批 107：拉取改经 `_get_supply_adapter(cfg).fetch_supply("static_list","symbols", …)`
    （原经 `engine._get_pro`）⇒ 桩改在 `_get_supply_adapter`（替身 adapter）。
    """

    def test_pull_failure_is_reported_not_raised(self):
        """拉取异常 → 返回 failed_dates（不抛）——原 beat 是 return {"status":"error"}，
        收编后必须转成 sync() 认的 failed_dates，否则会被当 success 推进游标。"""
        from src.data_sync.engine import _sync_static_list

        class _A:
            def fetch_supply(self, kind, sub_kind=None, **params):
                raise RuntimeError("boom")

        with patch("src.data_sync.engine._get_supply_adapter", return_value=_A()):
            r = _sync_static_list({"id": "static_symbols"}, "20260930")
        assert _RESULT_KEYS <= set(r)
        assert r["failed_dates"] and "boom" in r["failed_dates"][0]
        assert r["saved"] == 0

    def test_empty_df_is_not_a_failure(self):
        from src.data_sync.engine import _sync_static_list

        class _A:
            def fetch_supply(self, kind, sub_kind=None, **params):
                return pd.DataFrame()

        with patch("src.data_sync.engine._get_supply_adapter", return_value=_A()):
            r = _sync_static_list({"id": "static_symbols"}, "20260930")
        assert _RESULT_KEYS <= set(r) and not r["failed_dates"]

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_writes_to_real_db(self):
        """真库往返回归钉：假 DataFrame 进 → static_symbols 真出行 → 清理。"""
        from src.data_platform.db import get_conn
        from src.data_sync.engine import _sync_static_list
        code = "TT.B83BTEST"

        class _A:
            def fetch_supply(self, kind, sub_kind=None, **params):
                return pd.DataFrame(
                    [{"ts_code": code, "name": "批83b行为钉", "industry": "测试"}])

        try:
            with patch("src.data_sync.engine._get_supply_adapter", return_value=_A()):
                r = _sync_static_list({"id": "static_symbols", "provider": "tushare"}, "20260930")
            assert _RESULT_KEYS <= set(r)
            assert r["saved"] == 1, r
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT name, industry, list_status, delisted FROM static_symbols WHERE ts_code=%s",
                    (code,)).fetchone()
            assert row == ("批83b行为钉", "测试", "L", False)
        finally:
            with get_conn() as conn:
                conn.execute("DELETE FROM static_symbols WHERE ts_code=%s", (code,))
                conn.commit()


class TestConvertibleTermsHandler:
    """`_sync_convertible_terms`（原 convertible-terms-sync beat）。

    批 107：拉取改经 `_get_supply_adapter(cfg).fetch_supply("static_list","terms", …)`
    （原直连 `tushare_adapter.pull_convertible_bonds` / `pull_cb_basic`）⇒ 桩改在
    `_get_supply_adapter`（替身 adapter）。只盖「网络边」，其余逻辑（50 只上限、逐只失败不中断、
    jsonb 落库）全真跑。
    """

    @staticmethod
    def _patch(bonds, terms):
        """替身供给 adapter：无 `ts_code` 参数 → 清单 df；有 → 单只条款 df（或抛异常/空）。"""
        class _A:
            def fetch_supply(self, kind, sub_kind=None, **params):
                tc = params.get("ts_code")
                if tc is None:
                    if isinstance(bonds, Exception):
                        raise bonds
                    return pd.DataFrame({"ts_code": list(bonds)})
                v = terms(tc) if callable(terms) else terms
                if isinstance(v, Exception):
                    raise v
                return pd.DataFrame() if v is None else pd.DataFrame([v])
        return patch("src.data_sync.engine._get_supply_adapter", return_value=_A())

    def test_list_failure_is_reported(self):
        from src.data_sync.engine import _sync_convertible_terms
        with self._patch(RuntimeError("no net"), {}):
            r = _sync_convertible_terms({"id": "convertible_terms"}, "20260930")
        assert _RESULT_KEYS <= set(r)
        assert r["failed_dates"] and "pull_list" in r["failed_dates"][0]

    def test_per_bond_failure_does_not_abort_round(self):
        """逐只失败只记 failed 不中断（原 beat 的 continue 语义）。"""
        from src.data_sync.engine import _sync_convertible_terms

        def _terms(tc):
            if tc == "B.SH":
                raise RuntimeError("bad bond")
            return {"conv_price": 1.0} if tc == "A.SH" else None

        with self._patch(["A.SH", "B.SH", "C.SH"], _terms):
            r = _sync_convertible_terms({"id": "convertible_terms"}, "20260930")
        assert r["failed_dates"] == ["B.SH:RuntimeError"], r       # 只有中间那只失败
        assert r["pulled"] == 3

    def test_only_first_fifty_bonds(self):
        """单轮限 50 只（原代码 bonds[:50]）——不变量，防收编时被"顺手优化"成全量。"""
        import json

        from src.data_platform.data_source import TushareDataSource
        from src.data_sync.engine import _sync_convertible_terms
        zero = TushareDataSource(params=json.dumps({"rate_limits": {"cb_basic": 0}}))
        codes = [f"{i}.SH" for i in range(80)]
        calls = {"n": 0}

        class _A:
            def fetch_supply(self, kind, sub_kind=None, **params):
                if params.get("ts_code") is None:
                    return pd.DataFrame({"ts_code": codes})
                calls["n"] += 1
                return pd.DataFrame()

        with patch("src.data_sync.engine._get_rate_ds", return_value=zero), \
             patch("src.data_sync.engine._get_supply_adapter", return_value=_A()):
            _sync_convertible_terms({"id": "convertible_terms"}, "20260930")
        assert calls["n"] == 50

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_writes_to_real_db(self):
        """真库往返：terms JSON 真的落 convertible_terms。"""
        from src.data_platform.db import get_conn
        from src.data_sync.engine import _sync_convertible_terms
        code = "TT.B83CB"
        try:
            with self._patch([code], {"conv_price": 12.34, "备注": "中文不转义"}):
                r = _sync_convertible_terms({"id": "convertible_terms", "provider": "tushare"},
                                            "20260930")
            assert r["saved"] == 1, r
            with get_conn() as conn:
                got = conn.execute("SELECT terms FROM convertible_terms WHERE ts_code=%s",
                                   (code,)).fetchone()[0]
            assert got["conv_price"] == 12.34
            assert got["备注"] == "中文不转义"          # ensure_ascii=False 语义
        finally:
            with get_conn() as conn:
                conn.execute("DELETE FROM convertible_terms WHERE ts_code=%s", (code,))
                conn.commit()


class TestScheduleEquivalence:
    """调度口令等价（收编的行为等价判据之一）。"""

    EXPECTED = {
        # sync_id: (cron 串, trade_day_filter)
        # 前两条与原 beat 的 crontab **逐字**对齐；后两条原为 celery interval / crontab：
        #   pool-data-sync: schedule=300.0（interval 秒）→ */5 * * * *（周期等价，同 5 分钟）
        #   pool-data-full-calibrate: crontab(day_of_week=0, hour=4, minute=7) → 逐字
        # 0130（批 92）：pool_data 改 0 2 * * *——季度级/公告级数据不配 5 分钟轮（见该迁移）。
        "static_symbols": ("37 4 * * 0", "none"),            # crontab(day_of_week=0, hour=4, minute=37)
        "convertible_terms": ("43 3 * * *", "none"),         # crontab(hour=3, minute=43)
        "pool_data": ("0 2 * * *", "none"),                  # 0130：每日 02:00（原 */5，批 92 降频）
        "pool_data_full_calibrate": ("7 4 * * 0", "none"),   # crontab(day_of_week=0, hour=4, minute=7)
    }

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_cron_strings_match_retired_beats(self):
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            rows = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT id, schedule, trade_day_filter FROM sync_config WHERE id = ANY(%s)",
                (list(self.EXPECTED),)).fetchall()}
        for sid, expected in self.EXPECTED.items():
            assert rows.get(sid) == expected, f"{sid} 调度口令与退役 beat 不等价：{rows.get(sid)}"

    def test_beats_actually_retired_from_scheduler(self):
        """app.py 不得再挂这四条 beat（否则双调度：beat 与 data_sync_scheduler 各跑一次）。"""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "scheduler" / "app.py"
        text = src.read_text(encoding="utf-8")
        assert "tasks.convertible_terms_sync" not in text
        assert "tasks.static_list_sync" not in text
        # 批 83b 0119：池数据两条 beat 退役——pool_data_sync_task 仍在（手动入口），
        # 故断的是 beat 键名而非任务名
        assert '"pool-data-sync"' not in text
        assert '"pool-data-full-calibrate"' not in text

    def test_retired_tasks_removed_from_tasks_module(self):
        """tasks.py 不得再定义这两个 @app.task（防"改了 beat 忘了删任务"留双份实现）。"""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "scheduler" / "tasks.py"
        text = src.read_text(encoding="utf-8")
        assert "def convertible_terms_sync" not in text
        assert "def static_list_sync" not in text
