"""告警/通知 —— 企业微信/Discord/Server酱，分级路由+配额聚合。

所有模块通过 notify(level, title, body) 推送，不直接接触渠道。
"""

from __future__ import annotations
import os
import time
import hashlib
import logging
from typing import Literal
import redis
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("alert_notify")

Level = Literal["info", "warn", "critical"]


class AlertNotify:
    """告警/通知单例。"""

    _instance = None

    def __init__(self):
        self._redis = redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True,
        )
        self._channels = {
            "wechat_work": os.environ.get("WECHAT_WORK_WEBHOOK", ""),  # 企业微信群机器人
            "discord": os.environ.get("DISCORD_WEBHOOK", ""),
            "serverchan": os.environ.get("SERVERCHAN_KEY", ""),  # Server酱（备用）
        }

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
        msg = self._format(level, title, body)

        if target and self._channels.get(target):
            self._send(target, msg)
        else:
            logger.warning(f"无可用渠道({level}): {title}")

        self._record(alert_id, level, title, body, target)
        return alert_id

    def report(self, title: str, body: str, channel: str = "wechat_work") -> None:
        """盘后报告分发（info 级，完整内容）。"""
        self.notify("info", title, body, channel)

    # ── 路由 ──

    def _route(self, level: Level) -> str:
        """按级别路由渠道。优先企业微信+Discord，Server酱备用。"""
        if level == "critical":
            return "wechat_work"  # 即时
        elif level == "warn":
            return "wechat_work"
        return "wechat_work"  # info 也走企业微信（配额宽松）

    def _format(self, level: Level, title: str, body: str) -> str:
        emoji = {"info": "ℹ️", "warn": "⚠️", "critical": "🔴"}[level]
        return f"{emoji} [{level.upper()}] {title}\n{body}"

    # ── 发送 ──

    def _send(self, channel: str, msg: str) -> bool:
        url = self._channels.get(channel, "")
        if not url:
            return False
        # 配额检查（Server酱免费版日限）
        if channel == "serverchan" and self._quota_exceeded("serverchan"):
            logger.warning("Server酱日配额超限，跳过")
            return False
        try:
            if channel == "discord":
                httpx.post(url, json={"content": msg[:2000]}, timeout=10)
            elif channel == "wechat_work":
                httpx.post(url, json={"msgtype": "text", "text": {"content": msg}}, timeout=10)
            elif channel == "serverchan":
                httpx.post(f"https://sctapi.ftqq.com/{url}.send",
                           data={"title": msg[:32], "desp": msg}, timeout=10)
            return True
        except Exception as e:
            logger.error(f"发送失败({channel}): {e}")
            return False

    # ── 去重 + 配额 ──

    def _dedup_key(self, title: str, level: Level) -> str:
        return hashlib.md5(f"{title}:{level}".encode()).hexdigest()[:12]

    def _is_deduped(self, key: str) -> bool:
        """1min 内同标题去重。"""
        k = f"alert:dedup:{key}"
        if self._redis.exists(k):
            return True
        self._redis.setex(k, 60, "1")  # 60s 去重窗口
        return False

    def _append_body(self, key: str, body: str):
        """合并追加 body。"""
        k = f"alert:body:{key}"
        self._redis.append(k, f"\n---\n{body}")

    def _quota_exceeded(self, channel: str) -> bool:
        """Server酱免费版日配额检查。"""
        k = f"alert:quota:{channel}:{time.strftime('%Y%m%d')}"
        used = int(self._redis.get(k) or 0)
        limit = 5  # Server酱免费版 5 条/天
        if used >= limit:
            return True
        self._redis.incr(k)
        self._redis.expire(k, 86400)
        return False

    def _record(self, alert_id: str, level: Level, title: str, body: str, channel: str):
        """记录到 Valkey（供 Web 展示）。"""
        self._redis.hset(f"alert:{alert_id}", mapping={
            "level": level, "title": title, "body": body[:500],
            "channel": channel, "ts": str(time.time()),
        })
        self._redis.lpush("alert:history", alert_id)
        self._redis.ltrim("alert:history", 0, 999)  # 保留最近 1000 条