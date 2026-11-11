"""D32 领域模型 · 枚举（batch 172a）。

纯标准库，无第三方依赖（quant_common 层 0 纯度守门）。
取值与现有实现逐一对应，确保 P1b 映射零语义漂移（D32 §12.2「形状类以代码为准」）：

- `Action` / `OrderType` ↔ `adapters.Order.action/order_type` 的字符串
- `Direction` ↔ `adapters.Position.direction` 的 `direction_*` 资源 id（D4）
- `OrderStatus` ↔ `order_log.status` 词表（`trading.ORDER_FINAL_STATES` +
  `write_order_status` 的映射 + 提交侧的 submitted/send_failed）
"""

from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    """委托方向（A 股现货：SELL 即减仓；加密永续接入时须重新评估）。"""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class Direction(StrEnum):
    """持仓/成交方向。值带 `direction_` 前缀（D4 资源 id 化，防与其它 id 冲突）。"""

    LONG = "direction_long"
    SHORT = "direction_short"
    NET = "direction_net"


class OrderStatus(StrEnum):
    """委托状态。**取值与本仓 order_log 词表逐字一致**（不照抄 vnpy 枚举名）。

    来源对照（`strategy_runner/trading.py`）：
    - 提交侧：`submitted`（:639）、`send_failed`（:634，伪终态，可被迟到终态覆写）
    - 交易所回报：`partial` / `all_traded` / `cancelled` / `rejected`（:82-83）
    - `not_traded`：只在表达层使用（挂在场上未成交），**不写 order_log**——与
      `write_order_status`「SUBMITTING/NOTTRADED 不回写」的现行为一致。
    """

    SUBMITTED = "submitted"
    SEND_FAILED = "send_failed"
    NOT_TRADED = "not_traded"
    PARTIAL = "partial"
    ALL_TRADED = "all_traded"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# 终态集合：与 `trading.ORDER_FINAL_STATES` 同值（SQL 闭包=只进不退主防线）。
# 注意 `send_failed` **不是**终态（提交侧伪终态，可被实达终态覆写）。
FINAL_STATUSES: frozenset[OrderStatus] = frozenset({
    OrderStatus.ALL_TRADED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
})
