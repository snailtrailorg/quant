"""企微智能机器人 WebSocket 长连接自实现（批13）。

协议唯一依据：docs/reference/企微智能机器人协议参考.md（从官方 @wecom/aibot-node-sdk@1.0.7
dist 源码逐帧核出，2026-09-10）。关键语义：
- 端点 wss://openws.work.weixin.qq.com；认证帧 aibot_subscribe(body={bot_id,secret})
- 认证 errcode≠0=凭证错不可恢复 → raise WecomAuthError（runner 转 SystemExit 交 pool 退避）
- 心跳 30s ping；missedPong≥2 判异常断开重连；重连指数退避 1s→30s 封顶，认证成功重置
- 会话内回复无纯文本帧：aibot_respond_msg 统一流式（msgtype=stream，finish=true 单帧完成）
- 同 req_id 回复帧串行 + 等回执（对齐官方 SDK）
- 日志纪律（B-P2-4）：认证帧 body 含 secret——日志只打 cmd/errcode/req_id 前缀，禁整帧 body
"""
from __future__ import annotations
import asyncio
import json
import logging
import random
import uuid

import websockets

logger = logging.getLogger("im_bot.wecom_ws")

DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"
HEARTBEAT_S = 30
MAX_MISSED_PONG = 2
RECONNECT_BASE_S = 1.0
RECONNECT_MAX_S = 30.0
REPLY_ACK_TIMEOUT_S = 10


class WecomAuthError(Exception):
    """凭证错（botId/secret 无效）——重试不可恢复，进程应 fail-fast 退出。"""


def _req_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


class WecomAIBotWS:
    """单 bot 长连接。主循环 run() 阻塞（含重连）；工作线程回复走 reply_threadsafe。"""

    def __init__(self, bot_id: str, secret: str, on_message, ws_url: str = DEFAULT_WS_URL):
        """on_message(body: dict, req_id: str) —— aibot_msg_callback 的 body 与原帧 req_id
        （回复帧须透传该 req_id）。在 asyncio loop 线程里回调——调用方自行起线程处理慢逻辑。"""
        self._bot_id = bot_id
        self._secret = secret
        self._on_message = on_message
        self._ws_url = ws_url
        self._loop: asyncio.AbstractEventLoop | None = None
        self._authed_once = False   # 当前 _run_once 周期是否认证成功（退避重置依据，A-P1-1）
        self._replies: dict[str, asyncio.Future] = {}   # req_id → 回执 future（认证/心跳/回复共用响应通道）

    # ── 对工作线程的回复接口（run_coroutine_threadsafe 桥——盲审 A-P1-6）──
    def reply_threadsafe(self, req_id: str, content: str) -> bool:
        """会话内流式回复（finish=true 单帧）。返回是否成功（含回执 errcode==0）。"""
        if not self._loop:
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._reply(req_id, content), self._loop)
            return bool(fut.result(timeout=REPLY_ACK_TIMEOUT_S + 5))
        except Exception as e:
            logger.warning("企微回复失败 req_id=%s…: %s", req_id[:14], e)
            return False

    async def _reply(self, req_id: str, content: str) -> bool:
        body = {"msgtype": "stream",
                "stream": {"id": _req_id("stream"), "finish": True, "content": content}}
        return await self._send_and_wait("aibot_respond_msg", req_id, body, new_req=False)

    # ── 帧原语 ──
    async def _send_and_wait(self, cmd: str, req_id: str, body: dict, *, new_req: bool = True) -> bool:
        """发帧并等同 req_id 回执（errcode==0）。new_req=False=复用入参 req_id（响应型帧）。"""
        if new_req:
            req_id = _req_id(cmd)
        fut = asyncio.get_running_loop().create_future()
        self._replies[req_id] = fut
        try:
            await self._ws.send(json.dumps({"cmd": cmd, "headers": {"req_id": req_id}, "body": body}))
            try:
                resp = await asyncio.wait_for(fut, timeout=REPLY_ACK_TIMEOUT_S)
                return bool(resp)
            except asyncio.TimeoutError:
                # 盲审 A-P1-2：超时=网络态（传播给上层进重连循环），不能与 errcode≠0 混判为凭证错
                logger.warning("企微 %s 回执超时 req_id=%s…（视为网络态走重连）", cmd, req_id[:14])
                raise
        finally:
            self._replies.pop(req_id, None)

    def _resolve_reply(self, frame: dict) -> None:
        """响应帧（认证/心跳/回复回执——headers.req_id 匹配）→ future。"""
        req_id = (frame.get("headers") or {}).get("req_id", "")
        fut = self._replies.get(req_id)
        if fut is not None and not fut.done():
            fut.set_result(frame.get("errcode", 0) == 0)

    # ── 主循环 ──
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        backoff = RECONNECT_BASE_S
        while True:
            self._authed_once = False
            try:
                await self._run_once()
                # 正常服务端断开（disconnect 系统帧）→ 重连
            except WecomAuthError:
                raise   # 凭证错不重试（fail-fast）
            except Exception as e:
                logger.warning("企微连接异常将重连: %s", e)
            # 盲审 A-P1-1：健康会话（认证成功过）后的断线不应累进退避——重置（协议 §1 语义）
            if self._authed_once:
                backoff = RECONNECT_BASE_S
            delay = min(backoff, RECONNECT_MAX_S) + random.uniform(0, 1)
            logger.info("企微 %ss 后重连", round(delay, 1))
            backoff = min(backoff * 2, RECONNECT_MAX_S)
            await asyncio.sleep(delay)

    async def _run_once(self) -> None:
        async with websockets.connect(self._ws_url, ping_interval=None) as ws:
            self._ws = ws
            try:
                # 认证（失败 raise WecomAuthError——不进重连循环）
                ok = await self._send_and_wait(
                    "aibot_subscribe", "", {"bot_id": self._bot_id, "secret": self._secret})
                if not ok:
                    raise WecomAuthError(f"bot_id={self._bot_id[:6]}… 认证被拒（errcode≠0）")
                self._authed_once = True   # 健康会话标志（A-P1-1 退避重置依据）
                logger.info("企微认证成功 bot_id=%s…", self._bot_id[:6])
                heartbeat = asyncio.create_task(self._heartbeat(ws))
                try:
                    async for raw in ws:
                        try:
                            frame = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            logger.warning("企微帧解析失败（跳过）")
                            continue
                        await self._dispatch_frame(frame)
                finally:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
            finally:
                self._replies.clear()

    async def _dispatch_frame(self, frame: dict) -> None:
        """单帧分发（抽方法供单测直驱）。"""
        cmd = frame.get("cmd", "")
        if not cmd:
            self._resolve_reply(frame)   # 无 cmd=回执帧（认证/心跳/回复 ack）
        elif cmd == "aibot_msg_callback":
            try:
                self._on_message(frame.get("body") or {},
                                 (frame.get("headers") or {}).get("req_id", ""))
            except Exception:
                logger.exception("企微消息回调处理异常（不断连）")
        elif cmd == "aibot_event_callback":
            logger.info("企微事件帧忽略（enter_chat 等）event=%s",
                        (frame.get("body") or {}).get("msgtype"))
        else:
            logger.info("企微未知帧忽略 cmd=%s", cmd)

    async def _heartbeat(self, ws) -> None:
        """30s ping；连续 MAX_MISSED_PONG 次 ack 未回→断开（上层重连）。"""
        missed = 0
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                ok = await self._send_and_wait("ping", "", {})
                missed = 0 if ok else missed + 1
            except Exception:
                missed += 1
            if missed >= MAX_MISSED_PONG:
                logger.warning("企微心跳连续 %s 次无 ack——主动断开重连", missed)
                await ws.close()
                return
