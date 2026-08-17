"""ST7 hub 批次测试：MinuteAggregator 口径 + BarMsgState 消息分类（设计 14 v2）。

覆盖：分钟末标注（R-BR9）/volume 累计差分（S3）/untrusted 双门限（R-BR4）/
gen 分区 seq 语义（R-BR6/R-DL2）/去重分类（R-DL1）/fencing 拒旧代次（R-DL4）。
"""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.md_hub.main import MinuteAggregator
from src.strategy_runner.hub_worker import BarMsgState, frozen_allows

TZ = ZoneInfo("Asia/Shanghai")


def _tick(minute: int, second: float, price: float, vol_acc: float, amt_acc: float = 0.0):
    return SimpleNamespace(
        datetime=datetime(2026, 8, 17, 10, minute, int(second), int((second % 1) * 1e6), tzinfo=TZ),
        last_price=price, volume=vol_acc, turnover=amt_acc,
    )


class TestMinuteAggregator:
    def test_minute_end_labeling(self):
        """R-BR9：桶 [10:00,10:01) → ts=10:01:00（Tushare 分钟末口径，评审 F2）。"""
        agg = MinuteAggregator()
        assert agg.on_tick("600000.SHSE", _tick(0, 5, 10.0, 100)) is None
        bar = agg.on_tick("600000.SHSE", _tick(1, 2, 10.2, 300))  # 跨分钟 → finalize 上一桶
        assert bar is not None
        assert bar["ts"].hour == 10 and bar["ts"].minute == 1 and bar["ts"].second == 0

    def test_volume_cumulative_diff(self):
        """S3：XTP qty 当日累计 → 桶 volume=桶末累计−上桶末累计。"""
        agg = MinuteAggregator()
        agg.on_tick("X.SHSE", _tick(0, 5, 10.0, 1000))
        agg.on_tick("X.SHSE", _tick(0, 30, 10.1, 1500))
        bar1 = agg.on_tick("X.SHSE", _tick(1, 2, 10.2, 2000))
        assert bar1["volume"] == 1500  # minute-0 桶末累计 1500 − 上桶末(无=0)
        bar2 = agg.on_tick("X.SHSE", _tick(2, 2, 10.3, 3500))
        assert bar2["volume"] == 500   # minute-1 桶末累计 2000 − 上桶末 1500（3500 属 minute-2 桶）

    def test_ohlc(self):
        agg = MinuteAggregator()
        agg.on_tick("X.SHSE", _tick(0, 5, 10.0, 100))
        agg.on_tick("X.SHSE", _tick(0, 20, 10.5, 200))
        agg.on_tick("X.SHSE", _tick(0, 40, 9.8, 300))
        agg.on_tick("X.SHSE", _tick(0, 55, 10.2, 400))
        bar = agg.on_tick("X.SHSE", _tick(1, 2, 10.1, 500))
        assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (10.0, 10.5, 9.8, 10.2)

    def test_untrusted_sparse_bucket(self):
        """R-BR4 双门限：跨度<30s 且 tick<3 → untrusted；低频但跨度大不误报。"""
        agg = MinuteAggregator()
        agg.on_tick("X.SHSE", _tick(0, 10, 10.0, 100))
        agg.on_tick("X.SHSE", _tick(0, 12, 10.0, 200))   # 跨度 2s，count=2
        bar = agg.on_tick("X.SHSE", _tick(1, 2, 10.0, 300))
        assert bar["untrusted"] is True

        agg2 = MinuteAggregator()
        agg2.on_tick("Y.SHSE", _tick(0, 5, 10.0, 100))
        agg2.on_tick("Y.SHSE", _tick(0, 45, 10.0, 200))  # 跨度 40s
        bar2 = agg2.on_tick("Y.SHSE", _tick(1, 2, 10.0, 300))
        assert bar2["untrusted"] is False

    def test_flush_all(self):
        """S2：定时 flush 在桶（11:30/15:00 尾桶不依赖下一 tick）。"""
        agg = MinuteAggregator()
        agg.on_tick("X.SHSE", _tick(59, 30, 10.0, 100))
        bars = agg.flush_all()
        assert len(bars) == 1 and bars[0]["ts"].minute == 0 and bars[0]["ts"].hour == 11


class TestBarMsgState:
    def test_ok_sequence(self):
        st = BarMsgState()
        # 首条消息必然 gen_jump（从 0 建基线，触发暖机——设计行为）
        assert st.classify({"gen": 3, "seq": 1}) == "gen_jump"
        assert st.classify({"gen": 3, "seq": 2}) == "ok"
        assert st.classify({"gen": 3, "seq": 3}) == "ok"

    def test_dup_and_reorder(self):
        st = BarMsgState()
        st.classify({"gen": 3, "seq": 1})
        assert st.classify({"gen": 3, "seq": 1}) == "dup_or_reorder"
        assert st.classify({"gen": 3, "seq": 0}) == "dup_or_reorder"

    def test_gap_detection(self):
        st = BarMsgState()
        st.classify({"gen": 3, "seq": 1})
        assert st.classify({"gen": 3, "seq": 5}) == "gap"
        assert st.seq == 5
        assert st.classify({"gen": 3, "seq": 6}) == "ok"

    def test_gen_jump_resets_seq(self):
        """R-BR6：gen 跳变重置 seq 基线（hub 重启不误判乱序）。"""
        st = BarMsgState()
        st.classify({"gen": 3, "seq": 10})
        assert st.classify({"gen": 4, "seq": 1}) == "gen_jump"
        assert st.classify({"gen": 4, "seq": 2}) == "ok"

    def test_stale_gen_rejected(self):
        """R-DL4 fencing：旧 hub 复活消息被拒。"""
        st = BarMsgState()
        st.classify({"gen": 5, "seq": 1})
        assert st.classify({"gen": 4, "seq": 99}) == "stale_gen"


class TestFreezeGate:
    def test_sell_passes_when_frozen(self):
        assert frozen_allows("SELL", {"now": True}) is True

    def test_buy_rejected_when_frozen(self):
        assert frozen_allows("BUY", {"now": True}) is False

    def test_all_pass_when_not_frozen(self):
        assert frozen_allows("BUY", {"now": False}) is True
