"""同步心跳锁 -- 基于 Valkey 的进程存活检测，替代时间阈值猜僵尸。

进程被杀后锁 TTL 自然过期，下次同步能抢到锁，不再卡死。
last_status（DB）退化为"上次结果展示"，防重真相源在此锁。

用法:
    with SyncLock("astock_daily") as lock:
        if not lock.acquired:
            return {"status": "skipped", "reason": "上次同步仍在运行"}
        ... 同步逻辑 ...
        lock.heartbeat()  # 长任务里周期性刷新 TTL
"""

from __future__ import annotations
import os
import uuid
import threading
import redis
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("data_sync")

_TTL_SEC = 60          # 锁 TTL 60 秒，心跳每 20 秒刷一次（留 40 秒余量）
_HEARTBEAT_INTERVAL = 20  # 后台心跳间隔


class SyncLock:
    """Valkey 心跳锁。acquired=False 表示锁被别的进程持有。"""

    def __init__(self, sync_id: str, ttl: int = _TTL_SEC):
        self.sync_id = sync_id
        self.key = f"sync:lock:{sync_id}"
        self.token = str(uuid.uuid4())
        self.ttl = ttl
        self._r = None
        self.acquired = False
        self._heartbeat_ok = True
        self._hb_thread: threading.Thread | None = None
        self._hb_stop = threading.Event()

    def acquire(self) -> bool:
        """SET NX EX 抢锁。成功返回 True；锁已被持有返回 False。"""
        if self._r is None:
            self._r = redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
        self.acquired = self._r.set(self.key, self.token, nx=True, ex=self.ttl) is not None
        return self.acquired

    def start_heartbeat(self):
        """启动后台线程周期刷新 TTL，保证长任务期间锁不过期。"""
        if not self.acquired or self._hb_thread:
            return

        def _beat():
            while not self._hb_stop.wait(_HEARTBEAT_INTERVAL):
                # 只续自己的锁（token 校验，避免续到别人的）
                lua = (
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('expire', KEYS[1], ARGV[2]) "
                    "else return 0 end"
                )
                try:
                    self._r.eval(lua, 1, self.key, self.token, self.ttl)
                except Exception as e:
                    logger.warning("心跳异常: %s", e)
                    self._heartbeat_ok = False

        self._hb_thread = threading.Thread(target=_beat, daemon=True)
        self._hb_thread.start()

    def heartbeat(self):
        """同步方式手动刷新一次 TTL（无后台线程时用）。"""
        if not self.acquired:
            return
        lua = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
        )
        try:
            self._r.eval(lua, 1, self.key, self.token, self.ttl)
        except Exception as e:
            logger.warning("heartbeat 刷新异常: %s", e)

    def release(self):
        """释放锁。只删自己的（token 校验）。"""
        if not self.acquired:
            return
        self._hb_stop.set()
        lua = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        try:
            self._r.eval(lua, 1, self.key, self.token)
        except Exception as e:
            logger.warning("release 异常: %s", e)
        self.acquired = False

    def __enter__(self):
        self.acquire()
        if self.acquired:
            self.start_heartbeat()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False
