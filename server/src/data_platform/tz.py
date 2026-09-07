"""A 股时区工具（26 号收尾批 C：bar 表 ts 写入 +08:00 显式 aware）。

生产 PG timezone=Asia/Shanghai（2026-09-07 实测），naive 写与 aware 写存储一致；
本 helper 消除「依赖 PG 会话时区/DB 默认」的隐性耦合，不改存储语义。
"""
from __future__ import annotations

import zoneinfo
from datetime import datetime

SHANGHAI = zoneinfo.ZoneInfo("Asia/Shanghai")   # IANA，非固定 +08:00 offset（1986-1991 DST 期，盲审 B-P2）


def as_shanghai(dt: datetime) -> datetime:
    """naive datetime → Asia/Shanghai aware；已 aware → astimezone 归一（盲审 B-P2 不原样返回）。

    bar 表 ts 写入统一走此函数，消除对 PG 会话时区的隐性依赖。
    """
    return dt.replace(tzinfo=SHANGHAI) if dt.tzinfo is None else dt.astimezone(SHANGHAI)
