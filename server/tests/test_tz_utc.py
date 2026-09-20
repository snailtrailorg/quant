"""批 56b：ts 表示层 UTC 统一回归钉（29 号 §四预案 4——三混场景：hub 聚合/暖机拼接/日界沿）。

立法（28 §3.2 + 用户 2026-09-20 重申）：库/代码构造统一 UTC 绝对时刻，显示层换算。
timestamptz 存储与表示无关——本批全部改动是"表示统一+naive 清零"，时刻语义恒不变。
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.data_platform.tz import as_utc, as_shanghai, SHANGHAI


class TestAsUtc:
    """纯函数：naive 按上海解释（上游语义=本地 A 股时刻）；aware 归一 UTC。"""

    def test_naive_shanghai_semantics(self):
        # A 股 9:31 开盘首根 → UTC 01:31（用户立法示例语义）
        assert as_utc(datetime(2026, 9, 21, 9, 31)) == datetime(2026, 9, 21, 1, 31, tzinfo=timezone.utc)

    def test_aware_plus8_passthrough_same_instant(self):
        a = as_utc(datetime(2026, 9, 21, 9, 31))
        b = as_utc(datetime(2026, 9, 21, 9, 31, tzinfo=SHANGHAI))
        assert a == b and a.utcoffset() == timedelta(0)   # 同一时刻，统一 UTC 表示

    def test_already_utc_stable(self):
        u = datetime(2026, 9, 21, 1, 31, tzinfo=timezone.utc)
        assert as_utc(u) == u

    def test_roundtrip_shanghai(self):
        # 显示层换算链：UTC aware → 上海 aware 显示 9:31
        assert as_shanghai(as_utc(datetime(2026, 9, 21, 9, 31))).hour == 9


class TestEpochKeyCrossRepresentation:
    """暖机拼接混表示去重（切换日窗口：Valkey 存量 +08:00 ISO × 新流 UTC ISO × PG aware）。

    同一时刻三种表示 → 同一 epoch 键（29 号歧义消解=Unix 秒；ISO 字符串在两表示下不同串，
    直接字符串比较会去重失效双消费）。
    """

    def test_same_instant_same_key(self):
        from src.strategy_runner.hub_worker import _epoch_key, _norm_ts
        iso_cn = "2026-09-21T09:31:00+08:00"
        iso_z = "2026-09-21T01:31:00Z"
        iso_utc = "2026-09-21T01:31:00+00:00"
        dt_aware = datetime(2026, 9, 21, 9, 31, tzinfo=SHANGHAI)
        keys = {_epoch_key(v) for v in (iso_cn, iso_z, iso_utc, dt_aware)}
        assert keys == {_epoch_key(iso_cn)}           # 四种表示同刻同键
        assert _epoch_key("garbage") == ""            # 脏值空键（不参与去重）

    def test_norm_ts_uniform_utc_iso(self):
        from src.strategy_runner.hub_worker import _norm_ts
        assert _norm_ts("2026-09-21T09:31:00+08:00") == "2026-09-21T01:31:00+00:00"
        assert _norm_ts("2026-09-21T01:31:00Z") == "2026-09-21T01:31:00+00:00"
        assert _norm_ts(datetime(2026, 9, 21, 9, 31, tzinfo=SHANGHAI)) == "2026-09-21T01:31:00+00:00"

    def test_watermark_compat_legacy_iso(self):
        """水位兼容：存量 +08:00 ISO 水位 → epoch 键（恢复分支同款逻辑——防恒假去重）。"""
        from src.strategy_runner.hub_worker import _epoch_key
        legacy = "2026-09-18T15:00:00+08:00"
        assert _epoch_key(legacy).isdigit()
        assert int(_epoch_key(legacy)) > int(_epoch_key("2026-09-18T14:59:00+08:00"))


class TestHubAggregationUtc:
    """hub 聚合：流协议 UTC 表示（parts as_utc）——分钟末口径时刻等价（详钉在 test_hub_arch）。"""

    def test_minute_end_instant_equivalence(self):
        from src.md_hub.main import MinuteAggregator
        from types import SimpleNamespace
        agg = MinuteAggregator()
        t0 = SimpleNamespace(datetime=datetime(2026, 9, 21, 10, 0, 5, tzinfo=SHANGHAI),
                             last_price=10.0, volume=100, turnover=0.0)
        t1 = SimpleNamespace(datetime=datetime(2026, 9, 21, 10, 1, 2, tzinfo=SHANGHAI),
                             last_price=10.2, volume=300, turnover=0.0)
        agg.on_tick("600000.SHSE", t0)
        bar = agg.on_tick("600000.SHSE", t1)
        assert bar["ts"] == datetime(2026, 9, 21, 2, 1, 0, tzinfo=timezone.utc)  # 10:01 上海 = 02:01 UTC


class TestDayBoundaryUtc:
    """日界沿：astock 锚 15:00 上海 = 07:00 UTC——UTC 表示下沿判定时刻等价（day_anchor 表驱动钉在
    test_security_master；此处钉表示换算的沿语义）。"""

    def test_anchor_instant(self):
        # 15:00 上海收盘 → UTC 07:00；15:00 后的 bar（15:01 上海）在 UTC 表示下仍 > 锚
        anchor_utc = as_utc(datetime(2026, 9, 21, 15, 0))
        bar_after = as_utc(datetime(2026, 9, 21, 15, 1))
        bar_before = as_utc(datetime(2026, 9, 21, 14, 59))
        assert anchor_utc.hour == 7
        assert bar_before < anchor_utc < bar_after


class TestWarmupMergeBehavior:
    """暖机合入行为级（盲审 A 修：原钉只测原语缩水——_warmup_merge 提模块级后真行为可钉）。"""

    @staticmethod
    def _bar_fields(ts_iso):
        return {"ts": ts_iso, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100}

    def test_mixed_representation_merge_no_dup(self):
        """混表示拼接：hist=PG 形态（UTC ISO）×流=旧 +08:00 存量+新 UTC——同刻去重无重复无缺口。"""
        from src.strategy_runner.hub_worker import _warmup_merge
        hist = [{"ts": "2026-09-21T01:31:00+00:00"},   # PG 暖机（09:31 上海）
                {"ts": "2026-09-21T01:32:00+00:00"}]   # 09:32 上海
        entries = [                                           # xrevrange 序（新→旧）
            ("id3", self._bar_fields("2026-09-21T01:34:00+00:00")),   # 09:34 新流 UTC
            ("id2", self._bar_fields("2026-09-21T09:33:00+08:00")),   # 09:33 旧流存量（+08:00）
            ("id1", self._bar_fields("2026-09-21T01:31:00Z")),        # 09:31 与 PG 首根同刻（重复）
        ]
        out = _warmup_merge(hist, entries)
        assert len(out) == 4                                  # 31/32/33/34——同刻 31 不双计
        assert [h["ts"] for h in out[2:]] == ["2026-09-21T01:33:00+00:00", "2026-09-21T01:34:00+00:00"]

    def test_upto_truncation_epoch(self):
        from src.strategy_runner.hub_worker import _warmup_merge, _epoch_key
        entries = [("id2", self._bar_fields("2026-09-21T01:34:00+00:00")),
                   ("id1", self._bar_fields("2026-09-21T09:33:00+08:00"))]   # 01:33Z
        out = _warmup_merge([], entries, upto_ts=_epoch_key("2026-09-21T01:33:00+00:00"))
        assert len(out) == 1 and out[0]["ts"].startswith("2026-09-21T01:33")   # 只灌 ≤当前消息

    def test_day_boundary_two_bars_across_open(self):
        """日切双根：开盘前后两桶连续根（[9:30]/[9:31] 上海）——聚合按分钟桶不裂根不混根。"""
        from src.md_hub.main import MinuteAggregator
        from types import SimpleNamespace
        agg = MinuteAggregator()
        agg.on_tick("600000.SHSE", SimpleNamespace(
            datetime=datetime(2026, 9, 21, 9, 30, 40, tzinfo=SHANGHAI),
            last_price=10.0, volume=100, turnover=0.0))
        b1 = agg.on_tick("600000.SHSE", SimpleNamespace(   # 跨分钟 → finalize [9:30) 桶（分钟末标注 9:31）
            datetime=datetime(2026, 9, 21, 9, 31, 5, tzinfo=SHANGHAI),
            last_price=10.1, volume=300, turnover=0.0))
        [b2] = agg.flush_minute(9 * 60 + 31)               # [9:31) 桶（分钟末标注 9:32）
        assert b1["ts"] == datetime(2026, 9, 21, 1, 31, 0, tzinfo=timezone.utc)
        assert b2["ts"] == datetime(2026, 9, 21, 1, 32, 0, tzinfo=timezone.utc)

    def test_anchor_along_day_boundary(self):
        """沿判定组合：day_anchor（表驱动）×as_utc(bar ts) 方向——15:00 后归下一数据日语义。"""
        from src.data_platform.tz import as_utc
        anchor_sh = datetime(2026, 9, 21, 15, 0)      # 上海收盘锚
        bar_1459, bar_1501 = as_utc(datetime(2026, 9, 21, 14, 59)), as_utc(datetime(2026, 9, 21, 15, 1))
        anchor_utc = as_utc(anchor_sh)
        assert bar_1459 < anchor_utc <= bar_1501       # 沿=15:00 含前不含后归属判据


class TestReadWriteFunnel:
    """读写收口：naive 窗口参数/行 ts 不再裸进 PG（pin UTC 后裸 naive=错 8h——生死面）。"""

    def test_validate_bars_funnel(self):
        from src.data_platform.db import validate_bars
        row = ("600000.SHSE", "1min", datetime(2026, 9, 21, 9, 31), 1.0, 1.0, 1.0, 1.0, 100, 100_000.0, 1.0, "tushare")
        [r] = validate_bars([row])
        assert r[2] == datetime(2026, 9, 21, 1, 31, tzinfo=timezone.utc)

    def test_get_bars_window_funnel(self):
        """get_bars naive start/end → aware UTC 传参（mock conn 断言绑参——收口不依赖会话解释）。"""
        from unittest.mock import MagicMock, patch
        import src.data_platform.db as dbm
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False
        cur = MagicMock()
        cur.__enter__.return_value = cur
        cur.__exit__.return_value = False
        conn.cursor.return_value = cur
        cur.fetchall.return_value = []
        with patch.object(dbm, "get_conn", return_value=conn), \
             patch.object(dbm, "ensure_table"):
            dbm.get_bars("600000.SHSE", "1D", datetime(2026, 9, 1), datetime(2026, 9, 21))
        bind = cur.execute.call_args[0][1]
        assert bind[1].tzinfo is not None and bind[1].utcoffset() == timedelta(0)
        assert bind[2].tzinfo is not None and bind[2].utcoffset() == timedelta(0)
