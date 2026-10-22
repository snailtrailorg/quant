"""批 134：质量告警摘要（纯函数，无状态）。

为什么独立成模块：全量重建（engine.sync_all）逐标的调 `validate_bar_quality` ⇒ 每标的 0~6 条 issue
逐条 warning ⇒ 一轮十余万行挤爆 journald（实测 16 万行/小时）。改法=**计数随返回值携带 + 调用方
循环内局部累加 + 结束时一次成串**：不引入任何全局收集器（全局态会跨轮残留、无 reset 即无界）。

落点说明：本模块属层 1（data_platform），`data_sync.engine`（层 3）可 import；反向不行。
"""
from __future__ import annotations

COUNT_KEYS: tuple[str, ...] = ("dup", "zero_price", "suspended", "suspended_residual")
# 只有前三类进「issue 计数」（suspended = 正常停牌，不算问题）
ISSUE_KEYS: tuple[str, ...] = ("dup", "zero_price", "suspended_residual")


def empty_counts() -> dict[str, int]:
    """全零计数（键集固定——累加侧不得 KeyError）。"""
    return {k: 0 for k in COUNT_KEYS}


def add_counts(acc: dict[str, int], counts: dict[str, int] | None) -> dict[str, int]:
    """把单个标的的 counts 累加进 acc（就地更新并返回）。"""
    for k in COUNT_KEYS:
        acc[k] = acc.get(k, 0) + int((counts or {}).get(k, 0) or 0)
    return acc


def issue_total(counts: dict[str, int]) -> int:
    """该标的的「问题计数」（停牌不算问题）。"""
    return sum(int(counts.get(k, 0) or 0) for k in ISSUE_KEYS)


def summarize(label: str, counts: dict[str, int], *, symbols: int,
              with_issue: int, errors: int,
              top: list[tuple[str, int]] | None = None) -> str:
    """把一轮全量重建的累计计数渲染成**一行**（调用方只 logger.warning 这一条）。

    top 是「单标的 issue 数」降序前 N（symbol, 条数），用于定位异常最重的标的。
    """
    top_str = ", ".join(f"{s}({n})" for s, n in (top or [])[:5]) or "-"
    return (
        f"[quality] {label} 全量重建：覆盖 {symbols} 标的（有问题 {with_issue}）；"
        f"重复 {counts.get('dup', 0)} / 价0 {counts.get('zero_price', 0)} / "
        f"停牌 {counts.get('suspended', 0)} / 停牌残差 {counts.get('suspended_residual', 0)}；"
        f"校验器异常 {errors}；top: {top_str}"
    )
