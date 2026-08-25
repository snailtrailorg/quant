"""MdSessionSupervisor 接线矩阵测试（批 2）——MagicMock session/alert + 真计数器假钟。

矩阵（任务契约）：due→renew / 症状且 ready→renew+告警 / 恢复→on_recovered /
例行告警节奏 / 异常吞没。附 AlertPolicy 默认值锁（hub 现值=迁移行为不变）与
alerts 工厂件（make_alert/make_guard/make_valkey）。
"""
from unittest.mock import MagicMock, patch

from src.strategy_framework.runtime.alerts import AlertPolicy, make_alert, make_guard, make_valkey
from src.strategy_framework.runtime.mdlink import MdSessionSupervisor
from src.strategy_framework.runtime.pulse import SessionCounters


def _counters(*, in_session=True, count=0, enter_ts=1000.0, last_ts=0.0):
    """假钟基线对齐的计数器：字段全量覆写，zombie/stalled 判定确定化。"""
    c = SessionCounters()
    c.apply_edge(in_session)          # 建沿（tick 内的 apply_edge 即为无沿 no-op）
    c.sess_count = count
    c.sess_enter_ts = enter_ts
    c.sess_last_ts = last_ts
    return c


def _sup(session=None, counters=None, alert=None, t=1000.0, role="hub", policy=None):
    session = session or MagicMock()
    session.schedule_due.return_value = False
    session.retry_ready.return_value = False
    counters = counters if counters is not None else _counters()
    alert = alert if alert is not None else MagicMock()
    clock = {"t": t}
    sup = MdSessionSupervisor(session, counters, alert, role=role, policy=policy,
                              now=lambda: clock["t"])
    return sup, session, counters, alert, clock


class TestWiring:
    def test_schedule_due_renews_without_alert(self):
        """接线 1：定时续航到点 → renew（例行维护，不告警）。"""
        sup, session, _, alert, _ = _sup()
        session.schedule_due.return_value = True
        sup.tick(True, True)
        session.renew.assert_called_once()
        alert.assert_not_called()

    def test_zombie_symptom_ready_renews_and_alerts(self):
        """接线 2a：僵尸会话（进沿后零 tick 超宽限）且退避到点 → 告警+renew。"""
        sup, session, _, alert, _ = _sup(counters=_counters(count=0, enter_ts=1000.0), t=1701.0)
        session.retry_ready.return_value = True
        sup.tick(True, True)                      # 701s > 600s 宽限
        session.renew.assert_called_once()
        assert alert.call_count == 1
        assert "反应式重登" in alert.call_args.args[0]

    def test_stall_symptom_ready_renews_and_alerts(self):
        """接线 2b：断流超线（301s > 300s）且退避到点 → 告警+renew。"""
        sup, session, _, alert, _ = _sup(counters=_counters(count=5, last_ts=1000.0), t=1301.0)
        session.retry_ready.return_value = True
        sup.tick(True, True)
        session.renew.assert_called_once()
        assert alert.call_count == 1              # 仅反应式一条（断流例行告警首见不起报）

    def test_symptom_but_not_ready_no_renew(self):
        """症状在、退避未到点：不重登不告警（限频由 session 退避持有）。"""
        sup, session, _, alert, _ = _sup(counters=_counters(count=0), t=1701.0)
        session.retry_ready.return_value = False
        sup.tick(True, True)
        session.renew.assert_not_called()
        alert.assert_not_called()

    def test_no_trading_day_no_zombie(self):
        """交易日历为假：zombie 判死不成立（假日静默不触发反应式）。"""
        sup, session, _, alert, _ = _sup(counters=_counters(count=0), t=1701.0)
        session.retry_ready.return_value = True
        sup.tick(True, False)
        session.renew.assert_not_called()
        alert.assert_not_called()

    def test_recovery_calls_on_recovered(self):
        """接线 3：断流 < recover_window → on_recovered（清退避）。"""
        sup, session, _, _, _ = _sup(counters=_counters(count=5, last_ts=1000.0), t=1030.0)
        sup.tick(True, True)                      # stalled=30s < 60s
        session.on_recovered.assert_called_once()

    def test_no_recovery_without_baseline(self):
        """无基线（stalled=None）：不算恢复。"""
        sup, session, _, _, _ = _sup(counters=_counters(count=0, last_ts=0.0))
        sup.tick(True, True)
        session.on_recovered.assert_not_called()

    def test_no_recovery_beyond_window(self):
        """断流超窗口（100s > 60s）：不算恢复（数据未回来）。"""
        sup, session, _, _, _ = _sup(counters=_counters(count=5, last_ts=1000.0), t=1100.0)
        sup.tick(True, True)
        session.on_recovered.assert_not_called()


class TestAlertPacing:
    def test_zero_tick_alert_every_150s(self):
        """零 tick 例行告警：首见起算不告警，150s 到点一条（hub counter%30 等值）。"""
        sup, _, _, alert, clock = _sup(counters=_counters(count=0), t=1000.0)
        sup.tick(True, True)                      # 首见：起算
        clock["t"] = 1149.0
        sup.tick(True, True)                      # 149s：未到
        assert alert.call_count == 0
        clock["t"] = 1150.0
        sup.tick(True, True)                      # 150s：告警
        assert alert.call_count == 1
        assert "零 tick" in alert.call_args.args[0]
        clock["t"] = 1151.0
        sup.tick(True, True)                      # 限频窗内不重复
        assert alert.call_count == 1

    def test_stall_alert_every_30s(self):
        """断流例行告警：超症状线后首见起算，30s 节奏（hub counter%6 等值）。"""
        sup, session, _, alert, clock = _sup(counters=_counters(count=5, last_ts=1000.0), t=1301.0)
        session.retry_ready.return_value = False  # 隔离反应式告警
        sup.tick(True, True)                      # 301s：首见起算，不告警
        assert alert.call_count == 0
        clock["t"] = 1330.0
        sup.tick(True, True)                      # 29s：未到
        assert alert.call_count == 0
        clock["t"] = 1331.0
        sup.tick(True, True)                      # 30s：告警
        assert alert.call_count == 1
        assert "断流" in alert.call_args.args[0]

    def test_enter_edge_resets_anchor(self):
        """时段进沿清告警锚：新时段重新起算，不沿旧节奏（flapping 不瞬时复发）。"""
        sup, _, c, alert, clock = _sup(counters=_counters(count=0), t=1000.0)
        sup.tick(True, True)
        assert sup._anchors["zero"] == 1000.0
        clock["t"] = 1150.0
        sup.tick(True, True)
        assert alert.call_count == 1
        c.apply_edge(False)                       # 出沿（外部驱动时段变化）
        clock["t"] = 1151.0
        sup.tick(True, True)                      # 进沿：锚重置为 1151，不告警
        assert sup._anchors["zero"] == 1151.0
        assert alert.call_count == 1


class TestSwallow:
    def test_schedule_due_exception_swallowed(self):
        sup, session, _, _, _ = _sup()
        session.schedule_due.side_effect = RuntimeError("boom")
        sup.tick(True, True)                      # 不抛即通过

    def test_alert_exception_swallowed(self):
        """告警通道故障不反噬监督节拍（never-raise 由 tick 整体兜底）。"""
        sup, session, _, alert, _ = _sup(counters=_counters(count=0), t=1701.0)
        session.retry_ready.return_value = True
        alert.side_effect = RuntimeError("notify down")
        sup.tick(True, True)

    def test_renew_exception_swallowed(self):
        sup, session, _, _, _ = _sup()
        session.schedule_due.return_value = True
        session.renew.side_effect = RuntimeError("relogin boom")
        sup.tick(True, True)

    def test_apply_edge_exception_swallowed(self):
        counters = MagicMock()
        counters.apply_edge.side_effect = RuntimeError("edge boom")
        sup = MdSessionSupervisor(MagicMock(), counters, MagicMock(),
                                  now=lambda: 1000.0)
        sup.tick(True, True)


class TestPolicyAndFactory:
    def test_policy_defaults_match_hub(self):
        """默认值=hub 现值（迁移行为不变的锁）：600/300/150/30/60。"""
        p = AlertPolicy()
        assert (p.zombie_grace, p.stall_error, p.zero_tick_alert_period,
                p.stall_alert_period, p.recover_window) == (600.0, 300.0, 150.0, 30.0, 60.0)

    def test_policy_override(self):
        """阈值可调（runner/worker 批 3 各取各值）。"""
        sup, session, _, alert, _ = _sup(
            counters=_counters(count=5, last_ts=1000.0), t=1301.0,
            policy=AlertPolicy(stall_error=120.0, recover_window=30.0))
        session.retry_ready.return_value = True
        sup.tick(True, True)                      # 301s > 120s 自定义线
        session.renew.assert_called_once()

    def test_make_alert_critical_never_raises(self):
        with patch("src.strategy_framework.runtime.alerts.safe_notify") as sn:
            a = make_alert()
            a("T", "B")
            sn.assert_called_once_with("critical", "T", "B")
            sn.side_effect = RuntimeError("channel down")
            a("T2")                               # 不抛即通过

    def test_make_guard_intercepts_and_alerts(self):
        alert = MagicMock()
        deco = make_guard("t.handler", alert)

        @deco
        def boom():
            raise ValueError("x")

        boom()                                    # 不抛即通过
        assert alert.call_count == 1
        assert alert.call_args.args[0].startswith("handler 异常")

    def test_make_valkey_short_timeout(self):
        """VALKEY_URL 连接件：3s 短超时 + decode_responses（不真实连接）。"""
        r = make_valkey()
        kw = r.connection_pool.connection_kwargs
        assert kw.get("socket_timeout") == 3
        assert kw.get("decode_responses") is True


class TestCopyLockAndGrace:
    """双盲审 P1 收尾：告警文案与迁移前 hub 逐字对齐 + zombie_grace 经监督器透传。"""

    @staticmethod
    def _sup(alerts, *, policy=None, context=None):
        from src.strategy_framework.runtime.mdlink import MdSessionSupervisor
        session = MagicMock()
        counters = MagicMock()
        counters.apply_edge.return_value = False
        counters.stalled.return_value = None
        counters.zombie.return_value = False
        counters.sess_count = 0
        clock = {"t": 1000.0}
        sup = MdSessionSupervisor(
            session, counters, lambda t, b="": alerts.append((t, b)),
            role="hub", policy=policy, context=context, now=lambda: clock["t"])
        return sup, session, counters, clock

    def test_zero_tick_copy_matches_old_hub(self):
        """零tick告警：标题与「订阅 N 个标的。」前缀逐字还原（role=hub）。"""
        alerts = []
        sup, _, counters, clock = self._sup(alerts, context=lambda: "订阅 3 个标的。")
        sup.tick(True, True)                    # 首见起算
        clock["t"] += 200.0                     # 过 150s 节奏
        sup.tick(True, True)
        assert alerts, "零tick告警应已触发"
        title, body = alerts[0]
        assert title == "hub 交易时段零 tick（僵尸会话嫌疑）"
        assert body.startswith("订阅 3 个标的。 进程内自动重登中")

    def test_stall_copy_matches_old_hub(self):
        """断流告警标题含「行情 」前缀（老 hub 文案），与零tick标题不一致是有意还原。"""
        alerts = []
        sup, _, counters, clock = self._sup(alerts)
        counters.stalled.return_value = 400.0   # >300 触发
        counters.zombie.return_value = False
        sup.tick(True, True)
        clock["t"] += 60.0                      # 过 30s 节奏
        sup.tick(True, True)
        stall = [a for a in alerts if "断流" in a[0]]
        assert stall and stall[0][0] == "行情 hub tick 断流"

    def test_grace_passthrough_via_supervisor(self):
        """AlertPolicy.zombie_grace 经监督器透传到 counters.zombie（单一来源兑现）。"""
        from src.strategy_framework.runtime.alerts import AlertPolicy
        sup, _, counters, _ = self._sup([], policy=AlertPolicy(zombie_grace=300))
        sup.tick(True, True)
        assert counters.zombie.call_args.kwargs.get("grace") == 300
