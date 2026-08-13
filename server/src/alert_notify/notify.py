"""告警/通知 -- 统一走 MessageChannel（channel_config DB），分级路由+去重+配额。

PI1 迁移（2026-08-08）：_channels .env -> channel_config DB（get_channel）。
所有模块通过 notify(level, title, body) 推送，不直接接触渠道。
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


class AlertNotify:
    """告警/通知单例（PI1：渠道走 MessageChannel DB）。"""

    _instance = None

    def __init__(self):
        self._redis = redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True,
        )

    @classmethod
    def get(cls) -> "AlertNotify":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def notify(self, level: Level, title: str, body: str,
               channel: str | None = None) -> str:
        """发送告警。channel=None 按 level 路由。返回 alert_id。"""
        alert_id = self._dedup_key(title, level)
        # 去重：1min 内同标题合并
        if self._is_deduped(alert_id):
            self._append_body(alert_id, body)
            return alert_id

        target = channel or self._route(level)

        # PI1：走 MessageChannel（channel_config DB）
        from src.alert_notify.channel import get_channel
        ch = get_channel(target)
        if ch:
            if not self._quota_exceeded(target):
                ch.send(title, body, level)
        else:
            logger.warning(f"无可用渠道({level}/{target}): {title}（在 Web 消息通道页配 channel_config）")

        self._record(alert_id, level, title, body, target)
        return alert_id

    def report(self, title: str, body: str, channel: str = "wechat_work") -> None:
        """盘后报告分发（info 级，完整内容）。"""
        self.notify("info", title, body, channel)

    # ── 路由 ──

    def _route(self, level: Level) -> str:
        """按级别路由渠道（P3-4 分级：critical 走 discord+wechat，warn/info 走 wechat）。"""
        if level == "critical":
            return "discord"
        return "wechat_work"

    # ── 去重 + 配额 ──

    def _dedup_key(self, title: str, level: Level) -> str:
        return hashlib.md5(f"{title}:{level}".encode()).hexdigest()[:12]

    def _is_deduped(self, key: str) -> bool:
        """1min 内同标题去重。"""
        k = f"alert:dedup:{key}"
        if self._redis.exists(k):
            return True
        self._redis.setex(k, 60, "1")
        return False

    def _append_body(self, key: str, body: str):
        """合并追加 body。"""
        self._redis.append(f"alert:body:{key}", f"\n---\n{body}")
        self._redis.expire(f"alert:body:{key}", 86400)

    def _quota_exceeded(self, channel: str) -> bool:
        """日配额检查（#39，默认 100 条/天/渠道，ALERT_DAILY_QUOTA 可配）。"""
        k = f"alert:quota:{channel}:{time.strftime('%Y%m%d')}"
        used = int(self._redis.get(k) or 0)
        limit = int(os.environ.get("ALERT_DAILY_QUOTA", "100"))
        if used >= limit:
            logger.warning(f"渠道 {channel} 日配额超限 {limit}，跳过")
            return True
        self._redis.setnx(k, 0)
        self._redis.incr(k)
        self._redis.expire(k, 86400)
        return False

    def _record(self, alert_id: str, level: Level, title: str, body: str, channel: str):
        """记录到 Valkey（实时看板）+ PG alert_history（持久化，P3-5）。"""
        self._redis.hset(f"alert:{alert_id}", mapping={
            "level": level, "title": title, "body": body[:500],
            "channel": channel, "ts": str(time.time()),
        })
        self._redis.lpush("alert:history", alert_id)
        self._redis.ltrim("alert:history", 0, 999)
        # P3-5 持久化到 PG
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute("SELECT 1 FROM alert_history LIMIT 1")
                conn.execute("INSERT INTO alert_history (level, title, body, channel) VALUES (%s,%s,%s,%s)",
                             (level, title, body[:1000], channel))
                conn.commit()
        except Exception:
            pass
