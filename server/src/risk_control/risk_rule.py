"""风控规则抽象基类（平台化：别人实现接口加规则）。

接口：check(order, context) -> RiskCheckResult / get_params()。
实现：MaxPositionRule / MaxSingleOrderRule（当前 risk_control DEFAULT_RULES 的子集抽象）。
别人加规则：实现 RiskRule 子类 + DB 配置（type=...），不改 risk_control。
risk_control.check_order 后续可改为遍历 risk_rules 表调各 RiskRule.check。
"""
from __future__ import annotations
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger("risk_rule")


@dataclass
class RiskCheckResult:
    approved: bool
    reason: str = ""


class RiskRule(ABC):
    """风控规则接口。"""

    @abstractmethod
    def check(self, order: dict, context: dict) -> RiskCheckResult:
        """检查订单（context 含持仓/资金等）。"""

    @abstractmethod
    def get_params(self) -> dict:
        """规则参数（DB 化配置）。"""


class MaxPositionRule(RiskRule):
    """持仓限额（单标的最大仓位%）。"""
    def __init__(self, max_pct: float = 0.1):
        self.max_pct = max_pct
    def check(self, order, context):
        current = context.get("position_pct", 0)
        if current + (order.get("pct") or 0) > self.max_pct:
            return RiskCheckResult(False, f"超持仓限额 {self.max_pct}")
        return RiskCheckResult(True)
    def get_params(self):
        return {"max_pct": self.max_pct}


class MaxSingleOrderRule(RiskRule):
    """单笔上限（单笔最大金额）。"""
    def __init__(self, max_amount: float = 100000):
        self.max_amount = max_amount
    def check(self, order, context):
        amount = order.get("amount") or 0
        if amount > self.max_amount:
            return RiskCheckResult(False, f"超单笔限额 {self.max_amount}")
        return RiskCheckResult(True)
    def get_params(self):
        return {"max_amount": self.max_amount}


class DailyLossLimitRule(RiskRule):
    """日累计亏损上限。"""
    def __init__(self, max_loss: float = 50000):
        self.max_loss = max_loss
    def check(self, order, context):
        today_loss = context.get("today_loss") or 0
        if today_loss < -self.max_loss:
            return RiskCheckResult(False, f"超日亏损限额 {self.max_loss}")
        return RiskCheckResult(True)
    def get_params(self):
        return {"max_loss": self.max_loss}


_REGISTRY: dict[str, type[RiskRule]] = {
    "max_position": MaxPositionRule,
    "max_single_order": MaxSingleOrderRule,
    "daily_loss_limit": DailyLossLimitRule,
}


def get_rule(rule_type: str, params: dict | None = None) -> RiskRule | None:
    """实例化规则（params 从 risk_rules 表 params JSON）。"""
    cls = _REGISTRY.get(rule_type)
    if not cls:
        return None
    return cls(**(params or {}))


def load_rules_from_db() -> list[RiskRule]:
    """从 DB risk_rules 表加载启用的规则（risk_control.check_order 可调）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT type, params FROM risk_rules WHERE enabled=true")
            rows = cur.fetchall()
        rules = []
        for r in rows:
            params = json.loads(r[1]) if r[1] else {}
            rule = get_rule(r[0], params)
            if rule:
                rules.append(rule)
        return rules
    except Exception as e:
        logger.warning(f"读 risk_rules 失败: {e}")
        return []
