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


def _redis() -> redis.Redis:
    # 批18 盲审A-P0-2 同修：双 1s 超时——Valkey hung 时缺省无超时会永久阻塞调用线程
    #（notify 挂在实盘告警路径=冻结交易主流程；超时=跳过去重继续发送）
    return redis.Redis.from_url(
        os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
        socket_connect_timeout=1, socket_timeout=1, decode_responses=True)


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

    # 1.5 批18：站内 SSE 实时化——insert 成功即跨进程广播「有新通知」信号帧。
    #     payload 只带 type 不带内容（盲审A-P0-1：email/system 类仅 admin 可见，title 明文
    #     广播给全部 SSE 连接=破可见性矩阵；前端只触发重拉，可见性过滤仍在服务端）。
    #     去重命中/insert 失败路径在上方短路——不广播，与铃铛数据一致。
    if notif_id is not None:
        try:
            from src.quant_common.eventbus import bus   # 惰性导入（eventbus 顶层已 import redis，此处惰性=防冷启动顺序耦合非减依赖）
            bus.publish_cross_process(0, "notification", {})
        except Exception as e:   # noqa: BLE001
            logger.warning("notification SSE 广播失败（60s 轮询兜底）: %s", e)

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
    """订阅型报告分发（盘后报告等）。批39 B-P2-7（用户裁定改走订阅链）：站内记 info +
    订阅广播（dispatch.broadcast——跳 min_level 门槛的订阅推送，通道勾选/节流/配额照常）；
    旧 channel_config webhook 直推退役（Channels UI 批38 删）。channel 形参保留兼容。"""
    notify("info", "system", title, body[:2000])
    from src.alert_notify.dispatch import broadcast
    broadcast("system", title, body[:2000])


# 批39：_push_channel 已删（用户裁定死码连根——webhook 链整体退役；报告推送走 dispatch.broadcast）
# 批44 累积审：notify._quota_exceeded 已删（批39 _push_channel 随 webhook 退役后零消费——dispatch 有自己的实现）
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
