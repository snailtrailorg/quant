"""风控中心 —— 全局+分市场双层，Valkey 无状态熔断。

用法:
    from src.risk_control import RiskControl
    rc = RiskControl.get()
    decision = rc.check_order({"symbol":"600000.SHSE","action":"BUY"})
    rc.emergency_halt("测试熔断")
    print(rc.is_halted())
"""

from .risk import RiskControl, RiskDecision, RiskState

__all__ = ["RiskControl", "RiskDecision", "RiskState"]
