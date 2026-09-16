"""飞书 Webhook 路由 —— 挂载到 FastAPI 主应用。

3 秒超时：收到消息立即返回 {"code":0}，处理丢后台线程。
"""

from __future__ import annotations
import logging
import json
import concurrent.futures
from fastapi import APIRouter, Request, HTTPException
from .bot import (
    verify_event_signature, process_message_async, load_feishu_users,
)

logger = logging.getLogger("feishu_bot.router")

router = APIRouter(prefix="/lark", tags=["feishu"])

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)


@router.post("/webhook")
async def webhook(request: Request):
    """飞书事件订阅回调。3s 内必须返回。"""
    body = await request.body()
    body_str = body.decode("utf-8")
    data = json.loads(body_str)

    # 签名校验（P0 复审修正：官方事件算法 sha256(头ts+头nonce+EncryptKey+body)；Encrypt Key
    # 未配置时函数内跳过——纯 token 模式兼容，操作执行面在卡片路径已 fail-closed）
    timestamp = request.headers.get("X-Lark-Timestamp", "")
    nonce = request.headers.get("X-Lark-Nonce", "")
    if not verify_event_signature(timestamp, nonce, body_str, request.headers.get("X-Lark-Signature", "")):
        raise HTTPException(403, "签名校验失败")

    # URL 验证（首次配置回调）
    if "challenge" in data:
        return {"challenge": data["challenge"]}

    # 事件类型
    event = data.get("event", {})
    msg = event.get("message", {})
    open_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")

    if not msg or not open_id:
        return {"code": 0}

    # 提取文本
    content = msg.get("content", "{}")
    text = json.loads(content).get("text", "") if content else ""

    # 后台处理（立即返回，不阻塞 3s 超时）
    receive_id = msg.get("chat_id", open_id)
    _executor.submit(process_message_async, open_id, text,
                     "chat_id" if receive_id != open_id else "open_id",
                     receive_id, None, msg.get("chat_type", ""))   # 批11E：透传 chat_type（码绑定 p2p 判据）

    return {"code": 0}  # 立即返回，3s 内


@router.post("/card/callback")
async def card_callback(request: Request):
    """交互卡片回调——批29-4 退役为桩。

    原 HTTP 卡片确认路径钉"平台级 bot"（单 webhook URL 无法区分多 bot，owner 查询
    0073 后恒空全拒）；六轮裁定平台级概念废弃，卡片确认收敛 ws 面 per-bot 长连接
    （ws_client._card_gates 五闸）。保留 challenge echo 防遗留 app 配置校验 404。"""
    body = await request.body()
    try:
        data = json.loads(body.decode("utf-8"))
        if "challenge" in data:
            return {"challenge": data["challenge"]}
    except Exception:
        data = None
    if data is not None:
        logger.info("HTTP 卡片面已桩化（批29-4），忽略回调: event_type=%s",
                    (data.get("header") or {}).get("event_type", "?"))
    return {"code": 0}


@router.get("/test")
def test_endpoint():
    """测试端点（验证飞书模块可访问）。"""
    return {"status": "ok", "module": "feishu_bot", "users_loaded": len(load_feishu_users() or [])}