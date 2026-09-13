"""SSE 跨进程桥消费端（批18）——仅 web-api 进程装配（main.py on_event）。

订阅 Valkey `quant:sse:*` → 回填本地 bus（all→publish_all / u:{uid}→publish）。
设计要点（方案 v2 双盲审）：
- 主体独立于装配（可测）：`SseBridge` 类持有 stop 事件，`run()` 为线程主体；
  `handle_message(channel, payload)` 纯函数化——测试直接喂消息不需真 Valkey。
- 毒消息隔离：单条 parse/publish 异常吞掉不断流（pub/sub 不重放，断流 5s 退避代价大于丢帧）。
- 重连前再查 stop（防关停后 5s 幽灵重连——daemon 线程随进程消亡，实害小但不留尾巴）。
- 信任边界（批18 18E）：本频道只承载「触发重拉」语义的信号帧，永不承载免服务端校验的
  渲染数据/操作指令。
"""
from __future__ import annotations

import json
import logging
import os
import threading

import redis

logger = logging.getLogger("quant_common.sse_bridge")

RECONNECT_S = 5


class SseBridge:
    def __init__(self, bus):
        self.bus = bus
        self._stop = threading.Event()

    # ── 纯函数：单消息处理（可测）──
    def handle_message(self, channel: str, payload: str) -> None:
        """频道后缀路由 + JSON 解析；任何异常吞掉（毒消息不断流）。"""
        try:
            if channel == "quant:sse:all":
                ev = json.loads(payload) if payload else {}
                if isinstance(ev, dict) and ev.get("type"):
                    self.bus.publish_all(ev["type"], {k: v for k, v in ev.items() if k != "type"})
                return
            if channel.startswith("quant:sse:u:"):
                uid = int(channel.rsplit(":", 1)[1])
                ev = json.loads(payload) if payload else {}
                if isinstance(ev, dict) and ev.get("type"):
                    self.bus.publish(uid, ev["type"], {k: v for k, v in ev.items() if k != "type"})
        except Exception as e:   # noqa: BLE001
            logger.warning("sse_bridge 毒消息隔离（channel=%s）: %s", channel, e)

    # ── 线程主体 ──
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                r = redis.Redis.from_url(
                    os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                    socket_connect_timeout=2, socket_timeout=6,   # get_message(timeout=5) 内空转必须界时
                    decode_responses=True)
                pubsub = r.pubsub(ignore_subscribe_messages=True)
                pubsub.psubscribe("quant:sse:*")
                try:
                    while not self._stop.is_set():
                        # get_message 轮询而非 listen() 阻塞迭代——stop 每 5s 必达
                        #（listen 下无消息流时 stop 永不被检查，关停挂到连接断开为止）
                        msg = pubsub.get_message(timeout=5.0)
                        if msg and msg.get("type") == "pmessage":
                            self.handle_message(msg.get("channel") or "", msg.get("data") or "")
                finally:
                    try:
                        pubsub.close()
                    except Exception:   # noqa: BLE001
                        pass
            except Exception as e:   # noqa: BLE001
                if not self._stop.is_set():
                    logger.warning("sse_bridge 断开，%ss 后重连: %s", RECONNECT_S, e)
            if self._stop.wait(RECONNECT_S):   # 重连前再查 stop（防关停后幽灵重连）
                break

    def start(self) -> None:
        threading.Thread(target=self.run, name="sse-bridge", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


# 进程级单例（main.py on_event 装配；测试直接 new SseBridge(bus) 不经此）
_bridge: SseBridge | None = None


def ensure_bridge() -> SseBridge:
    """幂等启动（startup 每次调用只起一线程；uvicorn 单 worker 语义）。"""
    global _bridge
    if _bridge is None:
        from .eventbus import bus
        _bridge = SseBridge(bus)
        _bridge.start()
    return _bridge
