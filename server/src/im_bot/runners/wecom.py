"""企微 runner（批13B）——pool 子进程入口：python -m src.im_bot.runners.wecom {bid}。

凭证错=WecomAuthError→SystemExit(1)（交 pool 退避——SDK 语义：认证失败重试不可恢复）。
消息回调在 asyncio loop 线程触发——慢处理（LLM）起工作线程，回复经 reply_threadsafe 桥回。
"""
from __future__ import annotations
import asyncio
import logging
import sys
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s [wecom-runner] %(levelname)s %(message)s")
logger = logging.getLogger("im_bot.runners.wecom")

_REPLY_LIMIT = 3000   # 截断留平台侧（B-P2-2；企微 stream 为 Markdown，余量保守）


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        raise SystemExit("用法: python -m src.im_bot.runners.wecom <bot_id>（数字）")
    bid = int(sys.argv[1])

    from src.im_bot.credentials import get_bot_credentials
    creds = get_bot_credentials(bid)
    bot_id_s, secret = creds.get("bot_id", ""), creds.get("secret", "")
    if not bot_id_s or not secret:
        raise SystemExit(f"bot {bid} 凭证缺失（BotId/Secret），fail-fast 交 pool 退避")

    from src.im_bot.users import backfill_from_env
    backfill_from_env(bid)   # 与飞书 ws_client 同构：启动回填

    from src.im_bot.wecom_ws import WecomAIBotWS, WecomAuthError
    from src.im_bot.handlers import handle_incoming

    def on_message(body: dict, req_id: str) -> None:
        """asyncio loop 线程——快解析后慢处理丢工作线程（不阻塞心跳/收帧）。"""
        userid = (body.get("from") or {}).get("userid", "")
        chattype = body.get("chattype", "")
        msgtype = body.get("msgtype", "")
        if msgtype != "text":
            # 非文本 MVP：仍回一条引导（企微有回复通道，与钉钉静默策略不同——用户可感知）
            threading.Thread(target=lambda: ws.reply_threadsafe(
                req_id, "暂不支持该消息类型（当前仅支持文本）。"), daemon=True).start()
            return
        text = ((body.get("text") or {}).get("content") or "").strip()
        if not userid or not text:
            return
        threading.Thread(target=_process, daemon=True,
                         args=(userid, text, chattype, req_id)).start()

    def _process(userid: str, text: str, chattype: str, req_id: str) -> None:
        chat_type = "p2p" if chattype == "single" else "group"   # 归一（B-P2-3）
        try:
            handle_incoming("wecom", bid, userid, text,
                            reply=lambda t: ws.reply_threadsafe(req_id, t[:_REPLY_LIMIT]),
                            chat_type=chat_type)
        except Exception:
            logger.exception("企微消息处理失败 userid=%s…", userid[:8])

    ws = WecomAIBotWS(bot_id_s, secret, on_message=on_message)
    logger.info("企微 runner 起: bot=%s wecom_bot=%s…", bid, bot_id_s[:6])
    try:
        asyncio.run(ws.run())
    except WecomAuthError as e:
        raise SystemExit(f"bot {bid} 企微认证失败: {e}——fail-fast 交 pool 退避")


if __name__ == "__main__":
    main()
