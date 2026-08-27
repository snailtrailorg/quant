"""XReadSleeper（批 4b）：EngineLoop.sleeper 注入——xreadgroup block 读取 + 到期唤醒双节奏。

worker 的流消费与定时钩子共用一个循环的接线件：block 毫秒数 = min(500, 距下一钩子到期
剩余毫秒)——定时钩子不可能被繁忙流饿死（sleeper 至多 500ms 必返 → loop 每迭代走
dispatch，5s 钩子最坏延迟=500ms+单批处理时长，与旧 worker block=500 等值）。

never-raise 契约（设计 v2.1，双盲审 P1 双同）：
- ``__call__`` 边界**全异常不外抛**（含 on_batch 批处理回调内的异常）——loop 的 sleep 位
  无 try/except（loop.py run()），异常传穿将命中调用方 finally 的 ``os._exit(0)`` = 干净
  退出码 → systemd 不重启 → 任务静默死（2026-08-20 A3 事故类）；
- redis Timeout 类静默返回（BLOCK 超时/网络超时归一），其他类**吞后睡 1s 返回、下轮再试**
  （禁无界内旋重试——长断时 ``__call__`` 不返会饿死心跳/停止/看门狗钩子，与旧 worker
  catch-continue 结构等价）；
- NOGROUP 直接 ``os._exit(75)``（禁 sys.exit——SystemExit 传穿 run 会命中 finally 的
  ``os._exit(0)`` 吞成退出码 0=自设陷阱反噬；组已不存在无清理可跳）→ 交 systemd 重启 →
  run() 启动段组重建接手（复用 SA4 退避，替代旧 1Hz 告警死循环永不恢复）。

线程模型（设计写死）：单线程同步——on_batch 在 loop 线程内联执行，与钩子同线程
（frozen/history 裸 dict 无并发险）。**禁止后台线程**。
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("runtime.xsleeper")

BLOCK_CAP_MS = 500        # block 上限（毫秒）——定时钩子最坏延迟的封顶件
RETRY_SLEEP_S = 1.0       # 非 Timeout 异常吞后的强制退避（禁内旋）
NOGROUP_EXIT_CODE = 75    # 组不存在：交 systemd 重启走启动段组重建（SA4 分类，非 0 非 1）


class XReadSleeper:
    """EngineLoop.sleeper 协议实现：阻塞读流 + 到期唤醒双节奏。"""

    def __init__(self, r, stream: str, group: str, consumer: str, on_batch):
        self._r = r
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._on_batch = on_batch

    def __call__(self, seconds: float) -> None:
        block_ms = max(1, min(BLOCK_CAP_MS, int(seconds * 1000)))   # 双盲 B P1：钳 1ms 禁 BLOCK 0（Redis 协议=永久阻塞，逃逸靠 socket_timeout=3 巧合不可依赖）
        try:
            batch = self._r.xreadgroup(self._group, self._consumer, {self._stream: ">"},
                                       count=10, block=block_ms)
            if batch:
                self._on_batch(batch)   # loop 线程内联执行（单线程模型）
        except Exception as e:
            if "NOGROUP" in str(e):
                logger.critical("消费组 %s 不存在（NOGROUP），退出码 %d 交 systemd 重启组重建",
                                self._group, NOGROUP_EXIT_CODE)
                os._exit(NOGROUP_EXIT_CODE)
                return   # 生产不可达（os._exit 不返）；测试打桩时防落穿到重试睡
            if "Timeout" not in type(e).__name__:
                logger.warning("XREADGROUP 异常: %s", e)
                time.sleep(RETRY_SLEEP_S)   # 吞后睡 1s 返回，下轮再试
            # Timeout 类（BLOCK 超时/网络超时）静默返回——到期唤醒路径，不占重试预算
