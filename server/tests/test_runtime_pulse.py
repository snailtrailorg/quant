"""SessionCounters + HeartbeatWriter 测试（批 2）。

重点：时段沿 enter_ts 单点化（2026-08-25 事故 1 根修的骨架化）、
zombie 判定收敛到 md_session 纯函数、心跳超集原则。
"""
from unittest.mock import MagicMock


class TestSessionCounters:
    def _c(self):
        from src.strategy_framework.runtime.pulse import SessionCounters
        return SessionCounters()

    def test_enter_edge_writes_enter_ts_and_resets(self):
        c = self._c()
        c.on_data(True)                       # 时段内来数
        assert c.sess_count == 1
        assert c.apply_edge(True) is True     # 进沿
        assert c.sess_enter_ts > 0
        assert c.sess_count == 0 and c.sess_last_ts == 0.0   # 基线清零

    def test_exit_edge_resets_but_keeps_enter_ts(self):
        c = self._c()
        c.apply_edge(True)
        c.on_data(True)
        assert c.apply_edge(False) is False   # 出沿非进沿
        assert c.sess_count == 0
        assert c.sess_enter_ts > 0            # 保留（zombie 判定不看出沿态）

    def test_mid_session_start_counts_as_enter(self):
        """盘中启动（首调即 True）视为进沿——enter_ts=启动时刻（runner 语义）。"""
        c = self._c()
        assert c.apply_edge(True) is True
        assert c.sess_enter_ts > 0

    def test_on_data_counts_in_session_only(self):
        c = self._c()
        c.apply_edge(True)
        c.on_data(True)
        c.on_data(True)
        assert c.sess_count == 2
        c.apply_edge(False)                   # 出沿清基线
        assert c.sess_count == 0
        c.on_data(False)                      # 时段外数据不计数（跨日回放）
        assert c.sess_count == 0

    def test_zombie_delegates(self):
        c = self._c()
        c.apply_edge(True)
        now = c.sess_enter_ts + 700
        assert c.zombie(now=now, trading_day=True) is True      # 零 tick 超宽限
        c.on_data(True)
        assert c.zombie(now=now + 700, trading_day=True) is False  # 有 tick 不判死
        assert c.zombie(now=now, trading_day=False) is False    # 假日不判

    def test_stalled(self):
        c = self._c()
        assert c.stalled(now=2000.0) is None                   # 无基线
        c.apply_edge(True)
        c.on_data(True)
        assert c.stalled(now=c.sess_last_ts + 120) == 120


class TestHeartbeatWriter:
    def test_beat_writes_base_extra_ts(self):
        from src.strategy_framework.runtime.pulse import HeartbeatWriter
        r = MagicMock()
        w = HeartbeatWriter(r, "quant:hb:t", ttl=90, base=lambda: {"pid": 1, "gen": 5})
        w.beat(bars=3)
        r.hset.assert_called_once()
        ca = r.hset.call_args
        assert ca.args[0] == "quant:hb:t"
        m = ca.kwargs["mapping"]
        assert m["pid"] == "1" and m["gen"] == "5" and m["bars"] == "3" and "ts" in m
        r.expire.assert_called_once_with("quant:hb:t", 90)

    def test_beat_failure_not_fatal(self):
        from src.strategy_framework.runtime.pulse import HeartbeatWriter
        r = MagicMock()
        r.hset.side_effect = RuntimeError("valkey down")
        HeartbeatWriter(r, "k").beat()        # 不抛即通过

    def test_superset_principle(self):
        """超集锁：writer 输出必须包含消费方（collector）所需字段——由 base 提供时全量透传。"""
        from src.strategy_framework.runtime.pulse import HeartbeatWriter
        r = MagicMock()
        hub_fields = {"pid": 1, "gen": 5, "subs": 2, "ticks": 100, "bars": 50,
                      "sess_ticks": 30, "dropped_pg": 0, "last_tick_ts": 1.0}
        HeartbeatWriter(r, "k", base=lambda: dict(hub_fields)).beat()
        written = set(r.hset.call_args.kwargs["mapping"])
        assert set(hub_fields) <= written     # 旧字段一个不少（只增不改）
