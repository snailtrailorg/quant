"""时间工具（批 56b UTC 立法：库/代码构造统一 UTC 绝对时刻，显示层换算）。

timestamptz 列存绝对时刻与表示无关——本模块职责是**消灭 naive 值**：
pin TimeZone=UTC 后任何 naive datetime 进 PG 会被解释为 UTC（原按上海）=静默错 8h。
写/读收口在 db.py（validate_bars/窗口查询）；流协议（md_hub parts）同版本原子切 UTC 表示。
as_shanghai 保留：显示层换算+本地时段判定（服务器本地=A 股时刻语义）用。
"""
from __future__ import annotations

import zoneinfo
from datetime import datetime, timezone

SHANGHAI = zoneinfo.ZoneInfo("Asia/Shanghai")   # IANA，非固定 +08:00 offset（1986-1991 DST 期，盲审 B-P2）
UTC = timezone.utc


def as_utc(dt: datetime) -> datetime:
    """naive → 按 Asia/Shanghai 解释转 UTC aware（上游 naive 语义=本地 A 股时刻）；已 aware → astimezone(UTC)。

    批 56b：写/读 PG 的统一收口（naive 残留在 pin UTC 后会被错解释，唯一生死面）。
    """
    return (dt.replace(tzinfo=SHANGHAI)).astimezone(UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def as_shanghai(dt: datetime) -> datetime:
    """naive datetime → Asia/Shanghai aware；已 aware → astimezone 归一（盲审 B-P2 不原样返回）。

    显示层换算/本地时段判定用（写库路径批 56b 起改走 as_utc）。
    """
    return dt.replace(tzinfo=SHANGHAI) if dt.tzinfo is None else dt.astimezone(SHANGHAI)
