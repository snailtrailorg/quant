"""MdSessionSupervisor（批 2）：L2 会话监督器——hub 主循环 L2 段的骨架化。

内聚 MdSessionBase 契约的全部接线（hub main.py L2 段语义原样收编）：
- 定时续航：schedule_due → renew（预测性维护，开盘前换新鲜会话）；
- 反应式重登：症状（僵尸会话/断流超线）且退避到点 → 告警 + renew——进程内
  重登不重启进程（分层规则：退出只属于进程域故障，数据流症状永不触达 L1）；
- 恢复：断流 < recover_window → on_recovered（清退避，下轮症状从头起算）；
- 例行告警节奏：零 tick（150s）/断流（30s）双通道限频（hub counter%30/%6 等值）。

tick 整体 try/except：L2 无退出路径，异常只记日志（进程域故障由看门狗兜）。
告警节奏锚按「时段剧集」重置——进沿清锚、条件消失清锚（flapping 不瞬时复发）。
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from src.strategy_framework.runtime.alerts import AlertPolicy

logger = logging.getLogger("runtime.mdlink")


class MdSessionSupervisor:
    """L2 会话监督器：counters 出症状，session 负责自愈动作，alert 负责通知。"""

    def __init__(self, session, counters, alert, *,
                 role: str = "hub", policy: AlertPolicy | None = None,
                 context: Callable[[], str] | None = None,
                 now: Callable[[], float] | None = None):
        self._session = session
        self._counters = counters
        self._alert = alert
        self._role = role
        self.policy = policy or AlertPolicy()
        self._context = context   # 告警正文前缀（如订阅数）——hub 老文案「订阅 N 个标的。」
        self._now = now or time.time
        self._anchors: dict[str, float] = {"zero": 0.0, "stall": 0.0}   # 告警节奏锚

    def tick(self, in_session: bool, trading_day: bool) -> None:
        """主循环节拍（每 5-10s 一调）：沿→续航→反应式→恢复→例行告警。永不抛。"""
        try:
            self._tick(in_session, trading_day)
        except Exception as e:
            logger.warning("[%s] L2 监督 tick 异常（吞没，下轮重来）: %s", self._role, e)

    # ——— 内部（测试可直接驱动）———

    def _tick(self, in_session: bool, trading_day: bool) -> None:
        p = self.policy
        now = self._now()
        if self._counters.apply_edge(in_session):
            self._anchors = {"zero": 0.0, "stall": 0.0}   # 新时段新节奏（告警从沿起算）

        # 1) 定时续航（当日一次）：开盘前换新鲜会话——XTP 日切丢会话的预测性维护
        if self._session.schedule_due():
            logger.info("[%s] 定时续航：交易日开盘前重登 MD 会话", self._role)
            self._session.renew()

        # 2) 反应式重登（症状驱动 + 退避到点）：零 tick 超宽限=僵尸会话 / 断流超线
        stalled = self._counters.stalled(now=now)
        symptom = (self._counters.zombie(now=now, trading_day=trading_day,
                                         grace=p.zombie_grace)   # 双盲审 P1：透传，单一来源
                   or (stalled is not None and stalled > p.stall_error))
        if symptom and self._session.retry_ready():
            logger.warning("[%s] MD 症状驱动重登（僵尸会话/断流）", self._role)
            self._alert(f"{self._role} MD 反应式重登",
                        "盘中零 tick 超 10 分钟或断流超 5 分钟，进程内重登会话（不重启进程）。"
                        "持续未恢复请查 XTP 平台状态。")
            self._session.renew()

        # 3) 数据恢复 -> 清反应式退避（下轮症状从头起算）
        if stalled is not None and stalled < p.recover_window:
            self._session.on_recovered()

        # 4) 例行告警（双通道限频，hub counter%30/%6 等值节奏；首见只起算不告警）
        # 文案与老 hub 逐字对齐（双盲审 P1：断流标题「行情 hub tick 断流」/零tick正文订阅数前缀）
        prefix = f"{self._context()} " if self._context else ""
        if in_session and self._counters.sess_count == 0:
            if self._paced("zero", now, p.zero_tick_alert_period):
                self._alert(f"{self._role} 交易时段零 tick（僵尸会话嫌疑）",
                            f"{prefix}进程内自动重登中（定时续航/反应式，不重启进程）；"
                            f"持续未恢复请查 XTP 平台状态与 journalctl 中 [gw] 日志。")
        else:
            self._anchors["zero"] = 0.0
        if stalled is not None and stalled > p.stall_error:
            if self._paced("stall", now, p.stall_alert_period):
                logger.critical("[%s] tick 断流 %.0fs（时段内已收 %d 条，只告警不自杀）",
                                self._role, stalled, self._counters.sess_count)
                self._alert(f"行情 {self._role} tick 断流",
                            f"时段内已收 {self._counters.sess_count} 条后断流。"
                            f"进程内自动重登中；持续未恢复请查 XTP 平台状态"
                            f"（journalctl 滤 [gw] 看 MD 生命周期）。")
        else:
            self._anchors["stall"] = 0.0

    def _paced(self, key: str, now: float, period: float) -> bool:
        """限频判定：锚未设=首见（起算不告警，对齐 hub counter%N 首报在 +period）；
        距锚 >= period 到点返回 True 并重锚。"""
        ts = self._anchors[key]
        if ts == 0.0:
            self._anchors[key] = now
            return False
        if now - ts >= period:
            self._anchors[key] = now
            return True
        return False
