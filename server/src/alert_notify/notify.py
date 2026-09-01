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
    """外部通道主动推送规则：实盘紧急（risk+critical）+ 基础设施紧急（system+critical，
    2026-08-18 盲审 D-F6：health_monitor 的 dep_down/unit_down 类 critical 若只落站内铃铛，
    恰在最需要告警的时刻到不了人）。其余站内即可（订阅型走 report）。"""
    return level == "critical" and category in ("risk", "system")


def _redis() -> redis.Redis:
    return redis.Redis.from_url(
        os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)


def notify(level: Level, category: Category, title: str, body: str = "",
           source_ref: str | None = None, code: str | None = None) -> int | None:
    """通知统一入口：落 PG（站内铃铛可见）+ 按规则外部推送。返回通知 id；去重命中返回 None。

    SE1（F-52）：Valkey 故障时降级——跳过去重继续发（告警不能与被监控对象共死）。

    code（web 长尾批 2026-09-01）：通知类型稳定标识（如 l3.failed/frozen.intercept），
    前端 runbook 映射与结构化渲染的键。渐进打码——未打码调用点 None 兼容。
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
                "INSERT INTO notifications (level, category, title, body, source_ref, code) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (level, category, title, body[:2000], source_ref, code))
            conn.commit()
            notif_id = cur.fetchone()[0]
    except Exception as e:
        logger.error("notification insert failed: %s", e)

    # 2. 外部：批 7 订阅分发（2026-09-02）——warn/critical 交 dispatch 异步三通道（IM/邮件/短信，
    #    Celery 队列化，业务路径仅付一次 executor.submit）；info 到站内为止。
    #    旧 15min 外推节流已移入 dispatch（原子 SET NX）；零 enabled 订阅时 dispatch 内置
    #    过渡兜底沿用本模块 should_push_external/_push_channel 旧 webhook 规则。
    try:
        from src.alert_notify.dispatch import dispatch   # 惰性导入（B-P13：httpx/celery 不进 live-task 冷启动链）
        dispatch(level, category, title, body, code=code, notif_id=notif_id)
    except Exception as e:
        logger.warning("alert dispatch 提交失败（站内不受影响）: %s", e)
    return notif_id


def safe_notify(level: Level, title: str, body: str = "", code: str | None = None) -> None:
    """never-raise 包装（2026-08-19 模块归位 P 审：收编 runner/_alert、monitor._notify、
    alert_failed 三处重复的 try/except notify 模式——调用方不再自裹）。"""
    try:
        notify(level, "system", title, body, code=code)
    except Exception as e:
        logger.warning("safe_notify 发送失败（吞掉，调用方主流程不受影响）: %s", e)


def report(title: str, body: str, channel: str = "wechat_work") -> None:
    """订阅型报告分发（盘后报告等）：站内记 info + 外部照推（属用户订阅，不占主动推送规则）。"""
    notify("info", "system", title, body[:2000])
    _push_channel("info", title, body, channel=channel)


def _push_channel(level: Level, title: str, body: str, code: str | None = None,
                  channel: str | None = None, notif_id: int | None = None) -> bool:
    """外部通道推送（webhook 渠道：分级路由 + 日配额）。返回是否送达（批 7：过渡兜底回写 legacy 态用）。

    W3（2026-09-01）：code 在 RUNBOOK 时 body 尾部追加处置行。截断纪律（盲审 A/B-P1）：
    discord content 上限 2000 字符/企微 markdown 4096 字节——**先截原 body 再拼行**，
    处置行永不落截断区（超限发送失败被通道层吞=告警静默丢，违 D-F1）；组装在配额
    检查后（配额已尽免白拼）；顺手修既有隐患（原版外推传原始 body 未截）。
    """
    target = channel or ("discord" if level == "critical" else "wechat_work")
    from src.alert_notify.channel import get_channel
    ch = get_channel(target)
    if not ch:
        logger.warning("无可用渠道(%s/%s): %s（在 Web 消息通道页配 channel_config）", level, target, title)
        return False
    if _quota_exceeded(target):
        return False
    try:
        line = ""
        if code:
            from src.alert_notify.runbook import RUNBOOK
            rb = RUNBOOK.get(code)
            if rb:
                line = f"\n▸ 处置[{rb['label']}]: {rb['guide']}"
        # W3（盲审 A/B-P1a 修正版）：分通道截断——企微 markdown 限 4096 **字节**
        # （chars≠bytes：1900 汉字=5700B 超限，通道层无二次截断=静默丢）；discord
        # content 限 2000 字符。先截原 body 再拼行，处置行永不落截断区。
        if target == "discord":
            out_body = body[:max(1990 - len(line), 0)] + line
        else:
            budget = 3900 - len(line.encode("utf-8"))
            out_body = body.encode("utf-8")[:max(budget, 0)].decode("utf-8", "ignore") + line
        ch.send(title, out_body, level)
        return True
    except Exception as e:
        logger.error("channel send failed (%s): %s", target, e)
        return False


def _quota_exceeded(channel: str) -> bool:
    """日配额（#39，默认 100 条/天/渠道）。

    P1 修复（2026-08-20 双盲审计）：配额检查原无 try——Valkey 故障时异常穿透 notify
    到 safe_notify 被吞，critical 告警的外部推送恰在 Valkey 故障时刻必死（违背 D-F1）。
    存储故障按未超限放行（fail-open，与节流键同款——见本文件 84-86 行注释的承诺）。
    """
    try:
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
    except Exception as e:
        logger.warning("配额检查失败（fail-open 放行）: %s", e)
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
