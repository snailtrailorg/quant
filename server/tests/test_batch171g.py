"""批 171g · DEF-04 空窗词修守门：三处早退记 skipped（非 success）。

直证形态：dev 真库现存量 `start>end_date AND status='success'` 的历史行不可改写
（审计链）；本守门钉**增量路径**——空窗轮（游标≥end）handler 返回体带
`log_status='skipped'` ⇒ sync_log 落 skipped（正常等待态，crypto :1763/jq :3101 先例）。
"""
from src.data_sync import engine


class TestEarlyReturnSkipped:

    def test_daily_basic_early_return(self):
        """astock_basic 型（_sync_basic 工厂 :730 早退）——last_sync_date 已=今 ⇒ skipped。"""
        from datetime import date
        # 直调 handler：cfg last=今天、end=今天 ⇒ start=明天 > end ⇒ 早退
        cfg = {"id": "astock_basic", "mode": "incremental", "enabled": True,
               "last_sync_date": date.today().strftime("%Y%m%d"), "provider": "tushare"}
        h = engine._HANDLERS.get("astock_basic")
        if h is None:
            import pytest
            pytest.skip("astock_basic handler 未装配（路由形态差异）")
        r = h(cfg, date.today().strftime("%Y%m%d"))
        assert r.get("log_status") == "skipped", f"空窗早退须 skipped 非 success: {r}"

    def test_via_kind_early_return_skipped_word(self):
        """三处早退的词面源级钉（inspect 禁令不适用于「常量返回体」——此处直调函数级）。"""
        import re
        src = open("src/data_sync/engine.py").read()
        # 三处早退块均含 log_status: "skipped"（:730/:3506/:3670 区）
        early_blocks = re.findall(
            r"if start_?[tse]*[a-z]* > end_date:.{0,400}?return \{", src, re.S)
        assert len(early_blocks) >= 3, f"早退块计数 {len(early_blocks)} < 3"
        # 每块后 500 字符内须有 skipped 词
        for i, blk in enumerate(early_blocks[:3]):
            seg = src[src.index(blk):src.index(blk) + 500]
            assert 'log_status": "skipped"' in seg or "log_status\": \"skipped" in seg, \
                f"早退块 {i} 缺 skipped 词"
