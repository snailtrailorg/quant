"""AlertPolicy + 告警/守卫/Valkey 共享工厂（批 2）。

收编三引擎各自复制的 _alert/_guard/_valkey 三件套（hub 现值即默认值，迁移行为不变）：
- AlertPolicy：L2 监督阈值与告警节奏的单一来源（hub 现值做默认）；
- make_alert：safe_notify 包装（never-raise——告警永不反噬主流程）；
- make_guard：quant_common.guard 包装（事件线程零保护，F-26）；
- make_valkey：VALKEY_URL 短超时连接（3s——监控组件不能被存储拖死）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

from src.alert_notify.notify import safe_notify
from src.quant_common.guard import guard as _guard_base

logger = logging.getLogger("runtime.alerts")


@dataclass
class AlertPolicy:
    """L2 监督阈值与告警节奏（默认=hub 现值，批 2 迁移行为不变）。

    - zombie_grace：僵尸会话宽限——进沿后零 tick 超此值判死（避开竞价静默窗口）；
    - stall_error：断流症状线——有基线后断流超此值触发反应式重登（批 3 统一 120/300 双级）；
    - zero_tick_alert_period / stall_alert_period：两类例行告警节奏（hub 原值 150s/30s）；
    - recover_window：断流小于此值视为数据恢复（清反应式退避）。
    """

    zombie_grace: float = 600.0
    stall_error: float = 300.0            # hub 现值；批 3 统一 120/300 双级
    zero_tick_alert_period: float = 150.0
    stall_alert_period: float = 30.0
    recover_window: float = 60.0


def make_alert() -> Callable[[str, str], None]:
    """统一告警入口（hub/_alert 收编）：critical 级 safe_notify。

    never-raise 由本包装自持（safe_notify 内部已兜，这里再兜一层——
    告警通道任何故障都不反噬主流程，测试可打桩 safe_notify 抛错验证）。
    """

    def alert(title: str, body: str = "") -> None:
        try:
            safe_notify("critical", title, body)
        except Exception as e:
            logger.warning("告警发送失败（吞没）: %s", e)

    return alert


def make_guard(name: str, alert: Callable[[str, str], None]):
    """handler 守卫工厂（hub/_guard 收编）：异常拦截 + 告警钩子注入。"""
    return _guard_base(name, alert=lambda title, body="": alert(title, body))


def make_valkey():
    """Valkey 连接（hub/_valkey 收编）：VALKEY_URL 环境变量，短超时防监控组件被拖死。"""
    import redis
    return redis.Redis.from_url(
        os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
        decode_responses=True, socket_timeout=3)
