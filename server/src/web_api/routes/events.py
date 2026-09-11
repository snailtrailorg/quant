"""Web 后端 · SSE 推送端点（批14）。

GET /api/events——fetch + ReadableStream 客户端（EventSource 不能带 Authorization header）。
契约（方案 v2）：
- 首帧 hello；15s 心跳；30min max-age 后 bye 主动断（客户端无感重连）
- 取帧=asyncio.wait_for(q.get(), 15)——禁 asyncio.wait(queue)（ghost-getter 吞事件陷阱）
- 断开检测=Starlette StreamingResponse 内建 disconnect 监听（is_disconnected 轮询冗余不加）
- X-Accel-Buffering: no——nginx 响应级关缓冲（主保险，默认生效）
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..auth import require_authenticated
from src.quant_common.eventbus import bus, CLOSE_SENTINEL

router = APIRouter(tags=["events"])

HEARTBEAT_S = 15
MAX_AGE_S = 30 * 60


@router.get("/api/events")
def events_stream(payload: dict = Depends(require_authenticated)):
    uid_raw = payload.get("sub")   # 盲审 A-P2-5：无 sub 的旧 token → 401 而非 KeyError 500
    if uid_raw is None:
        from ..errors import ApiError
        raise ApiError(401, "AUTH_INVALID", "凭证无效")
    uid = int(uid_raw)

    async def _gen():
        q = bus.subscribe(uid)
        started = time.monotonic()
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if time.monotonic() - started > MAX_AGE_S:
                    yield "event: bye\ndata: {}\n\n"   # max-age 主动断（客户端无感重连）
                    return
                try:
                    item = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"   # 心跳（注释行）
                    continue
                if item is CLOSE_SENTINEL:
                    return   # 停机哨兵（lifespan shutdown bus.close）
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(uid, q)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
