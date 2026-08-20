"""飞书 Webhook 路由 —— 挂载到 FastAPI 主应用。

3 秒超时：收到消息立即返回 {"code":0}，处理丢后台线程。
"""

from __future__ import annotations
import os
import logging
import json
import threading   # P0-2：卡片确认执行一直缺 import（确认功能 NameError 坏死——审计 B 服务层）
import concurrent.futures
from fastapi import APIRouter, Request, HTTPException
from .bot import (
    verify_event_signature, check_user, process_message_async,
    execute_confirmed_tool, FeishuClient, load_feishu_users,
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
    _executor.submit(process_message_async, open_id, text, "chat_id" if receive_id != open_id else "open_id")

    return {"code": 0}  # 立即返回，3s 内


@router.post("/card/callback")
async def card_callback(request: Request):
    """交互卡片回调（用户点确认/取消）。"""
    body = await request.body()
    data = json.loads(body.decode("utf-8"))

    # URL 验证
    if "challenge" in data:
        return {"challenge": data["challenge"]}

    # SD2（F-33）重放防护：event_id 5 分钟内只接受一次（伪造/重发的回调直接丢弃）
    event_id = data.get("event_id") or data.get("header", {}).get("event_id")
    if event_id:
        try:
            import redis as _redis
            r = _redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
            if not r.set(f"feishu:card:{event_id}", "1", nx=True, ex=300):
                logger.warning("重复卡片回调丢弃: event_id=%s", event_id)
                return {"code": 0}
        except Exception as e:
            logger.warning("卡片去重检查失败（放行，风险自负）: %s", e)

    # 提取卡片按钮值
    action_data = data.get("event", {}).get("action", {}).get("value", {})
    if action_data.get("action") == "confirm":
        # SD2（F-33）时效校验：60s 窗口外（含无 ts 的旧卡片）拒绝执行
        from .bot import card_action_fresh
        if not card_action_fresh(action_data):
            logger.warning("卡片确认超时/无时间戳拒绝执行: tool=%s", action_data.get("tool"))
            return {"code": 0}
        open_id = data.get("event", {}).get("operator", {}).get("open_id", "")
        tool = action_data.get("tool", "")
        args = action_data.get("args", {})
        # P0-2 修复（2026-08-20 双盲审计 C1 严重）：原回调无验签+执行无授权——伪造卡片可触发
        # 熔断/恢复/策略启停（event_id/ts 均可自造）。三道闸：
        # ①签名（LARK_VERIFICATION_TOKEN 未配置 → 拒绝执行，fail-closed 取代原跳过）
        # P0 复审修正（2026-08-20）：官方卡片签名=sha1(HTTP 头 ts + 头 nonce + token + body)
        _ts = request.headers.get("X-Lark-Timestamp", "")
        _nonce = request.headers.get("X-Lark-Nonce", "")
        _sig = request.headers.get("X-Lark-Signature", "")
        from .bot import verify_card_signature, check_user
        if not os.environ.get("LARK_VERIFICATION_TOKEN", ""):
            logger.error("卡片确认拒绝执行：LARK_VERIFICATION_TOKEN 未配置（fail-closed，2026-08-20 P0）")
            return {"code": 0}
        if not verify_card_signature(_ts, _nonce, body.decode("utf-8"), _sig):
            logger.warning("卡片签名校验失败拒绝执行: open_id=%s tool=%s", open_id, tool)
            return {"code": 0}
        # ②操作者授权：操作类工具需 trader/admin（与 Web 侧权限矩阵对齐——resume 原为 admin 专属）
        _role = check_user(open_id)
        if _role not in ("trader", "admin") or tool == "risk_resume" and _role != "admin":
            logger.warning("卡片确认权限不足拒绝执行: open_id=%s role=%s tool=%s", open_id, _role, tool)
            return {"code": 0}
        threading.Thread(
            target=execute_confirmed_tool,
            args=(open_id, tool, json.dumps(args) if isinstance(args, dict) else args),
            daemon=True,
        ).start()

    return {"code": 0}  # 立即返回


@router.get("/test")
def test_endpoint():
    """测试端点（验证飞书模块可访问）。"""
    return {"status": "ok", "module": "feishu_bot", "users_loaded": len(load_feishu_users() or [])}