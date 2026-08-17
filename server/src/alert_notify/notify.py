"""通知中心 —— 站内（PG notifications 表）+ 按规则外部推送（MessageChannel）。

2026-08-14 设计决策（详见 flow/decisions.md）：
- 所有事件统一 notify(level, category, title, body) → 落 PG（持久、重启不丢、前台铃铛可见）
- 类别×角色可见矩阵（谁能在铃铛/通知历史里看到）：
    email → admin；risk/task → admin+trader；data → admin+analyst；system → admin
- 外部通道（企微/Discord 等）只主动推「实盘紧急」= risk+critical；其余仅站内（订阅型推送未来扩展）
- 去重（1min 同 title+level+category，Valkey）+ 渠道日配额保留
- report()（盘后报告）为订阅型：站内记 info + 外部照推
- 替代原 Valkey alert:history（易失）；旧 AlertNotify 类废弃移除
"""

from __future__ import annotations
import os
import time
import hashlib
import logging
from typing import Literal
import redis
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("alert_notify")

Level = Literal["info", "warn", "critical"]
Category = Literal["email", "risk", "task", "data", "system"]

# 类别 × 角色可见矩阵（铃铛/通知历史按当前用户角色过滤）
CATEGORY_ROLES: dict[str, list[str]] = {
    "email": ["admin"],                 # 邀请/开通邮件失败 → 仅 admin 看得懂
    "risk": ["admin", "trader"],        # 熔断/漂移/对账异常
    "task": ["admin", "trader"],        # 任务失败/卡死（影响交易执行）
    "data": ["admin", "analyst"],       # 数据断连/同步异常
    "system": ["admin"],                # 接口健康/磁盘/通道
}


def visible_categories(role: str) -> list[str]:
    """当前角色可见的通知类别。"""
    return [c for c, roles in CATEGORY_ROLES.items() if role in roles]


def should_push_external(category: str, level: str) -> bool:
    """外部通道主动推送规则：仅实盘紧急（risk+critical）。其余站内即可（订阅型走 report）。"""
    return category == "risk" and level == "critical"


def _redis() -> redis.Redis:
    return redis.Redis.from_url(
        os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)


def notify(level: Level, category: Category, title: str, body: str = "",
           source_ref: str | None = None) -> int | None:
    """通知统一入口：落 PG（站内铃铛可见）+ 按规则外部推送。返回通知 id；去重命中返回 None。

    SE1（F-52）：Valkey 故障时降级——跳过去重继续发（告警不能与被监控对象共死）。
    """
    try:
        r = _redis()
        key = f"notify:dedup:{hashlib.md5(f'{title}:{level}:{category}'.encode()).hexdigest()[:12]}"
        if r.exists(key):
            return None  # 1min 内同标题去重
        r.setex(key, 60, "1")
    except Exception as e:
        logger.warning("去重键不可用（Valkey 故障？），跳过去重继续发送: %s", e)

    # 1. 站内：落 PG
    notif_id = None
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO notifications (level, category, title, body, source_ref) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (level, category, title, body[:2000], source_ref))
            conn.commit()
            notif_id = cur.fetchone()[0]
    except Exception as e:
        logger.error("notification insert failed: %s", e)

    # 2. 外部：仅实盘紧急
    if should_push_external(category, level):
        _push_channel(level, title, body)
    return notif_id


def report(title: str, body: str, channel: str = "wechat_work") -> None:
    """订阅型报告分发（盘后报告等）：站内记 info + 外部照推（属用户订阅，不占主动推送规则）。"""
    notify("info", "system", title, body[:2000])
    _push_channel("info", title, body, channel=channel)


def _push_channel(level: Level, title: str, body: str, channel: str | None = None) -> None:
    """外部通道推送（分级路由 + 日配额）。"""
    target = channel or ("discord" if level == "critical" else "wechat_work")
    from src.alert_notify.channel import get_channel
    ch = get_channel(target)
    if not ch:
        logger.warning("无可用渠道(%s/%s): %s（在 Web 消息通道页配 channel_config）", level, target, title)
        return
    if _quota_exceeded(target):
        return
    try:
        ch.send(title, body, level)
    except Exception as e:
        logger.error("channel send failed (%s): %s", target, e)


def _quota_exceeded(channel: str) -> bool:
    """日配额（#39，默认 100 条/天/渠道）。"""
    r = _redis()
    k = f"alert:quota:{channel}:{time.strftime('%Y%m%d')}"
    used = int(r.get(k) or 0)
    limit = int(os.environ.get("ALERT_DAILY_QUOTA", "100"))
    if used >= limit:
        logger.warning("渠道 %s 日配额超限 %s，跳过", channel, limit)
        return True
    r.setnx(k, 0)
    r.incr(k)
    r.expire(k, 86400)
    return False


def cleanup(retention_acked_days: int = 7, retention_all_days: int = 30) -> dict:
    """留存清理（beat 每日）：已确认>7天删除，全部>30天删除。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur1 = conn.execute("DELETE FROM notifications WHERE status='acked' AND acked_at < now() - make_interval(days=>%s)",
                            (retention_acked_days,))
        cur2 = conn.execute("DELETE FROM notifications WHERE created_at < now() - make_interval(days=>%s)",
                            (retention_all_days,))
        conn.commit()
        return {"acked_expired": cur1.rowcount, "all_expired": cur2.rowcount}
