"""批 83b：两条收编 beat 的行为级钉（真库往返 + 调度口令等价）。

收编的验收判据是"**行为等价**"（任务书 83b 验收 2），故本文件钉三件：
1. **真库往返**——handler 真的把行写进 `static_symbols` / `convertible_terms`
   （只把**网络边**打桩：tushare 调用返回假 DataFrame；DB 写路径全真跑。
   这是"mock 绿"的反面：mock 只允许盖住无法在 CI 调用的外部 API，不允许盖住被测逻辑）。
2. **返回契约**——`sync()` 吃的结果字典键齐（pulled/saved/start/failed_dates/
   expected_days/actual_days）——缺键会被 sync() 当 0 处理并推进游标（数据洞）。
3. **调度口令等价**——新 sync_config 行的 cron 串与原 beat 的 crontab **逐字一致**，
   且 `trade_day_filter='none'`（原 beat 无交易日过滤）——填错=行为变化。
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
    """`_sync_static_list`（原 static-list-sync beat）。"""

    def test_pull_failure_is_reported_not_raised(self):
        """拉取异常 → 返回 failed_dates（不抛）——原 beat 是 return {"status":"error"}，
        收编后必须转成 sync() 认的 failed_dates，否则会被当 success 推进游标。"""
        from src.data_sync.engine import _sync_static_list
        with patch("src.data_sync.engine._get_pro", side_effect=RuntimeError("boom")):
            r = _sync_static_list({"id": "static_symbols"}, "20260930")
        assert _RESULT_KEYS <= set(r)
        assert r["failed_dates"] and "boom" in r["failed_dates"][0]
        assert r["saved"] == 0

    def test_empty_df_is_not_a_failure(self):
        from src.data_sync.engine import _sync_static_list
        from unittest.mock import MagicMock
        pro = MagicMock()
        pro.stock_basic.return_value = pd.DataFrame()
        with patch("src.data_sync.engine._get_pro", return_value=pro):
            r = _sync_static_list({"id": "static_symbols"}, "20260930")
        assert _RESULT_KEYS <= set(r) and not r["failed_dates"]

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_writes_to_real_db(self):
        """真库往返回归钉：假 DataFrame 进 → static_symbols 真出行 → 清理。"""
        from unittest.mock import MagicMock
        from src.data_platform.db import get_conn
        from src.data_sync.engine import _sync_static_list
        code = "TT.B83BTEST"
        pro = MagicMock()
        pro.stock_basic.return_value = pd.DataFrame(
            [{"ts_code": code, "name": "批83b行为钉", "industry": "测试"}])
        try:
            with patch("src.data_sync.engine._get_pro", return_value=pro):
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
    """`_sync_convertible_terms`（原 convertible-terms-sync beat）。"""

    def test_list_failure_is_reported(self):
        from src.data_sync.engine import _sync_convertible_terms
        with patch("src.data_platform.adapters.tushare_adapter.pull_convertible_bonds",
                   side_effect=RuntimeError("no net")):
            r = _sync_convertible_terms({"id": "convertible_terms"}, "20260930")
        assert _RESULT_KEYS <= set(r)
        assert r["failed_dates"] and "pull_list" in r["failed_dates"][0]

    def test_per_bond_failure_does_not_abort_round(self):
        """逐只失败只记 failed 不中断（原 beat 的 continue 语义），且限 50 只。"""
        from src.data_sync.engine import _sync_convertible_terms
        with patch("src.data_platform.adapters.tushare_adapter.pull_convertible_bonds",
                   return_value=["A.SH", "B.SH", "C.SH"]), \
             patch("src.data_platform.adapters.tushare_adapter.pull_cb_basic",
                   side_effect=[{"conv_price": 1.0}, RuntimeError("bad bond"), {}]):
            r = _sync_convertible_terms({"id": "convertible_terms"}, "20260930")
        assert r["failed_dates"] == ["B.SH:RuntimeError"], r       # 只有中间那只失败
        assert r["pulled"] == 3

    def test_only_first_fifty_bonds(self):
        """单轮限 50 只（原代码 bonds[:50]）——不变量，防收编时被"顺手优化"成全量。

        这里把限速档配成 0（走**真实** DataSource + 真实 FixedIntervalPolicy 的 DB 覆写
        路径，`get_interval` 对覆写值取 `max(0.0, x)`），否则 50 次调用要真睡 15s——
        不是把限速器 mock 掉，而是给它一份合法的零间隔配置。
        """
        import json

        from src.data_platform.data_source import TushareDataSource
        from src.data_sync.engine import _sync_convertible_terms
        zero = TushareDataSource(params=json.dumps({"rate_limits": {"cb_basic": 0}}))
        codes = [f"{i}.SH" for i in range(80)]
        with patch("src.data_sync.engine._get_rate_ds", return_value=zero), \
             patch("src.data_platform.adapters.tushare_adapter.pull_convertible_bonds",
                   return_value=codes), \
             patch("src.data_platform.adapters.tushare_adapter.pull_cb_basic",
                   return_value={}) as pb:
            _sync_convertible_terms({"id": "convertible_terms"}, "20260930")
        assert pb.call_count == 50

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_writes_to_real_db(self):
        """真库往返：terms JSON 真的落 convertible_terms。"""
        from src.data_platform.db import get_conn
        from src.data_sync.engine import _sync_convertible_terms
        code = "TT.B83CB"
        try:
            with patch("src.data_platform.adapters.tushare_adapter.pull_convertible_bonds",
                       return_value=[code]), \
                 patch("src.data_platform.adapters.tushare_adapter.pull_cb_basic",
                       return_value={"conv_price": 12.34, "备注": "中文不转义"}):
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
        # sync_id: (cron 串, trade_day_filter) —— 与原 beat 的 crontab 逐字对齐
        "static_symbols": ("37 4 * * 0", "none"),      # 原 crontab(day_of_week=0, hour=4, minute=37)
        "convertible_terms": ("43 3 * * *", "none"),   # 原 crontab(hour=3, minute=43)
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
        """app.py 不得再挂这两个 beat（否则双调度：beat 与 data_sync_scheduler 各跑一次）。"""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "scheduler" / "app.py"
        text = src.read_text(encoding="utf-8")
        assert "tasks.convertible_terms_sync" not in text
        assert "tasks.static_list_sync" not in text

    def test_retired_tasks_removed_from_tasks_module(self):
        """tasks.py 不得再定义这两个 @app.task（防"改了 beat 忘了删任务"留双份实现）。"""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "scheduler" / "tasks.py"
        text = src.read_text(encoding="utf-8")
        assert "def convertible_terms_sync" not in text
        assert "def static_list_sync" not in text
