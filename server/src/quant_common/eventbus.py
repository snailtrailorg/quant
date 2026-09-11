"""进程内事件总线（批14 SSE）——层 0 通用件，纯 stdlib。

并发契约（方案 v2 盲审 A-P2-6/B-P2-4）：
- watchers 的读改写全部经 call_soon_threadsafe 收敛到 loop 内执行——publish 调用线程
  （如 onboarding daemon 线程、sync 端点线程池）只做调度不直接迭代 dict
  （防 changed-size-during-iteration）
- loop 惰性锚定：首次 subscribe 时 get_running_loop()（web startup 是 sync def 无 loop）
- loop 未锚定/已关闭：publish 一律 no-op（停机窗口 daemon 线程 publish 不炸调用方）

跨进程桥（通知中心等 celery 产生者）接口预留：publish_cross_process() no-op，
Valkey pub/sub 消费线程接入时填充（uvicorn 多 worker 化时同）。
"""
from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger("quant_common.eventbus")

QUEUE_MAX = 50        # 满→丢最旧（状态事件可合并，旧态无消费价值）
CLOSE_SENTINEL = None  # 停机哨兵（close() 投给全部 watcher，SSE 生成器收即退）


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watchers: dict[int, list[asyncio.Queue]] = {}

    # ── loop 协程侧调用 ──
    def subscribe(self, uid: int) -> asyncio.Queue:
        """注册 watcher（必须在 loop 协程内调——惰性锚定 loop）。
        盲审 A-P1-2：旧 loop 关闭后新 loop 重锚（原 if None 单向锚定——pytest 每测新 loop
        会顺序依赖性静默丢事件；生产单 loop 不触发但这是基础设施件）。"""
        if self._loop is None or self._loop.is_closed():
            if self._loop is not None:
                self._watchers.clear()   # 旧 loop 的死队列残骸清空
            self._loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        with self._lock:
            self._watchers.setdefault(uid, []).append(q)
        return q

    def unsubscribe(self, uid: int, q: asyncio.Queue) -> None:
        """断开清理（finally 必达；loop 已关时也能安全摘除——纯 dict 操作）。"""
        with self._lock:
            lst = self._watchers.get(uid)
            if lst and q in lst:
                lst.remove(q)
                if not lst:
                    self._watchers.pop(uid, None)

    # ── 任意线程调用 ──
    def publish(self, uid: int, ev_type: str, data: dict) -> None:
        """同步发布，永不 raise（推送故障绝不炸调用方——_set_session 契约②）。"""
        try:
            self._dispatch(uid, {"type": ev_type, **data})
        except Exception as e:   # noqa: BLE001
            logger.warning("eventbus publish 失败（不影响调用方）: %s", e)

    def close(self) -> None:
        """停机钩子：向全部 watcher 投哨兵。
        盲审 A-P1-1 实测：uvicorn 0.52 停机顺序=先超时 cancel 任务再跑 lifespan——SSE 生成器
        被强 cancel 后哨兵才到（对 SSE 连接永远迟到）；停机释放实际由 systemd graceful 超时
        单独承担，哨兵仅兜底非 SSE 消费者（未来非流式 watcher）。"""
        try:
            self._dispatch_all(CLOSE_SENTINEL)
        except Exception as e:   # noqa: BLE001
            logger.warning("eventbus close 失败: %s", e)

    # ── 内部 ──
    def _dispatch(self, uid: int, item) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return   # 未锚定/已关：no-op（轮询兜底覆盖）
        with self._lock:
            queues = list(self._watchers.get(uid, ()))
        if not queues:
            return
        def _put():
            for q in queues:
                self._put_drop_oldest(q, item)
        loop.call_soon_threadsafe(_put)

    def _dispatch_all(self, item) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            all_queues = [q for lst in self._watchers.values() for q in lst]
        def _put():
            for q in all_queues:
                self._put_drop_oldest(q, item)
        loop.call_soon_threadsafe(_put)

    @staticmethod
    def _put_drop_oldest(q: asyncio.Queue, item) -> None:
        if q.full():
            try:
                q.get_nowait()   # 丢最旧（状态事件可合并）
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(item)

    def publish_cross_process(self, uid: int, ev_type: str, data: dict) -> None:
        """跨进程桥预留（Valkey pub/sub）——本批 no-op，接入时实现并改 publish 双写。"""
        return None


bus = EventBus()   # 进程单例
