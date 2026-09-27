"""批 62b：shadow 引擎测试钉（白名单归一 diff/预算/验证义务——验收①④，A03 §15.1 治理三条）。"""
from unittest.mock import MagicMock, patch


def _row(sym, d, close=10.0, vol=1000, adj=1.0):
    return {"symbol": sym, "trade_date": d, "open": 10.0, "high": 10.0, "low": 10.0,
            "close": close, "volume": vol, "amount": vol * 10, "adj_factor": adj}


class TestDiffRows:
    def test_identical_no_diff(self):
        from src.data_platform.quality import diff_rows
        rows = diff_rows([_row("A", "2026-09-25")], [_row("A", "2026-09-25")])
        assert rows == []

    def test_price_beyond_tolerance_real_diff(self):
        from src.data_platform.quality import diff_rows
        rows = diff_rows([_row("A", "2026-09-25", close=10.0)], [_row("A", "2026-09-25", close=10.5)])
        real = [r for r in rows if not r["whitelist_hit"]]
        assert real and real[0]["field"] == "close" and real[0]["main_val"] == 10.0

    def test_price_within_abs_tolerance_ok(self):
        from src.data_platform.quality import diff_rows
        rows = diff_rows([_row("A", "2026-09-25", close=10.00)], [_row("A", "2026-09-25", close=10.005)])
        assert rows == []   # abs 0.01 内容差

    def test_adj_null_main_whitelisted(self):
        """白名单 adj-factor-degraded：主值 NULL=降级写入→命中不进真 diff。"""
        from src.data_platform.quality import diff_rows
        m = _row("A", "2026-09-25", adj=None)
        b = _row("A", "2026-09-25", adj=1.02)
        rows = diff_rows([m], [b])
        adj_rows = [r for r in rows if r["field"] == "adj_factor"]
        assert adj_rows and adj_rows[0]["whitelist_hit"] == "adj-factor-degraded"

    def test_volume_within_afterhours_whitelist(self):
        """白名单 afterhours-volume：量差 5% 内命中（口径差非错账）。"""
        from src.data_platform.quality import diff_rows
        rows = diff_rows([_row("A", "2026-09-25", vol=10000)], [_row("A", "2026-09-25", vol=10300)])
        vol_rows = [r for r in rows if r["field"] == "volume"]
        assert vol_rows and vol_rows[0]["whitelist_hit"] == "afterhours-volume"

    def test_missing_row_whitelisted_log_only(self):
        """__missing__ 白名单 log_only：记命中不进告警计数（停牌窗含其中——人工报表区分）。"""
        from src.data_platform.quality import diff_rows
        rows = diff_rows([_row("A", "2026-09-25")], [])
        assert rows and rows[0]["field"] == "__missing__" and rows[0]["whitelist_hit"] == "suspended-missing"

    def test_extra_row_real_diff(self):
        from src.data_platform.quality import diff_rows
        rows = diff_rows([], [_row("A", "2026-09-25")])
        assert rows and rows[0]["field"] == "__extra__" and rows[0]["whitelist_hit"] is None

    def test_disabled_entry_not_loaded(self):
        """生效=false 条目不加载（冷切换——B-P1-6 挂账形态）。"""
        from src.data_platform.quality import load_whitelist
        ids = [e["id"] for e in load_whitelist()]
        assert "coldswitch-bar-gap" not in ids


class TestBudgetFloor:
    def test_floor_skip_and_alert(self):
        """预算耗尽两倍：跳过执行+升级告警（A-P1-6——不静默砍零）。"""
        from src.data_platform import quality as Q
        conn = MagicMock(); conn.__enter__.return_value = conn
        pol = MagicMock(fetchone=MagicMock(return_value=("tushare", 5, None, 100, True)))
        conn.execute.return_value = pol
        # 预算查询返回 250（=2.5 倍预算）
        seq = [pol, MagicMock(fetchone=MagicMock(return_value=(250,)))]
        conn.execute.side_effect = seq
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch("src.data_platform.quality.safe_notify") as sn:
            r = Q.run_shadow_check("bar_daily")
        assert r["ran"] is False and "预算" in r["skipped_reason"]
        assert sn.called and sn.call_args.kwargs.get("code") == "quality.budget-exhausted"


class TestWhitelistVerifyCheck:
    def test_overdue_alerts_real_db(self):
        """真库行为级（mock 链失灵教训——61b 同款）：写入过期验证记录→检出→告警→清理。"""
        from src.data_platform import quality as Q
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("INSERT INTO system_config (key, value) VALUES (%s,%s) "
                         "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                         ("whitelist_verify:adj-factor-degraded", "2026-06-01 抽核3标的仍NULL"))
            conn.commit()
        try:
            with patch("src.data_platform.quality.safe_notify") as sn:
                overdue = Q.whitelist_verify_check()
            assert any(o["id"] == "adj-factor-degraded" for o in overdue)
            assert sn.called and sn.call_args.kwargs.get("code") == "quality.whitelist-stale"
        finally:
            with get_conn() as conn:
                conn.execute("DELETE FROM system_config WHERE key=%s",
                             ("whitelist_verify:adj-factor-degraded",))
                conn.commit()
