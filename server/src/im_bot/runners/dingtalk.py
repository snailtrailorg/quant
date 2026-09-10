"""钉钉 Stream runner（批13）——pool 子进程入口：python -m src.im_bot.runners.dingtalk {bid}。

凭证预检 fail-fast（盲审 A-P0-3）：SDK start() 主循环 except Exception 无限重试不退出——
凭证错若直接起 SDK=僵尸子进程（pool 退避挂在进程退出上永不触发）。故起 SDK 前先拉
accessToken，失败即 SystemExit(1) 交 pool 退避；首连看门狗双保险（Event 撤防）。

AsyncChatbotHandler：sync process 跑在 SDK 内置 ThreadPoolExecutor(8)+立即 ACK——
LLM 慢调用占池即天然背压，无需再起线程（盲审 A-P2-9）。
"""
from __future__ import annotations
import logging
import sys
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s [dt-runner] %(levelname)s %(message)s")
logger = logging.getLogger("im_bot.runners.dingtalk")

_FIRST_CONNECT_WATCHDOG_S = 300   # 首连看门狗：5 分钟无任何消息帧即退出（交 pool 退避）


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        raise SystemExit("用法: python -m src.im_bot.runners.dingtalk <bot_id>（数字）")
    bid = int(sys.argv[1])

    from src.im_bot.credentials import get_bot_credentials
    creds = get_bot_credentials(bid)
    app_key, app_secret = creds.get("app_key", ""), creds.get("app_secret", "")
    if not app_key or not app_secret:
        raise SystemExit(f"bot {bid} 凭证缺失（AppKey/AppSecret），fail-fast 交 pool 退避")

    # 凭证预检（A-P0-3）：凭证错=SystemExit，不让 SDK 进入无限重连僵尸态
    from src.im_bot.dingtalk import fetch_access_token
    token, err = fetch_access_token(app_key, app_secret)
    if not token:
        raise SystemExit(f"bot {bid} 凭证预检失败: {err}——fail-fast 交 pool 退避")
    logger.info("bot %s 凭证预检通过（accessToken OK）", bid)

    import dingtalk_stream
    from src.im_bot.handlers import handle_incoming

    first_frame = threading.Event()

    def _watchdog():
        import os
        import signal
        if first_frame.wait(_FIRST_CONNECT_WATCHDOG_S):
            return   # 已成连，撤防
        logger.error("bot %s 首连看门狗超时（%ss 未成连），退出交 pool 退避",
                     bid, _FIRST_CONNECT_WATCHDOG_S)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_watchdog, daemon=True).start()

    class _Handler(dingtalk_stream.AsyncChatbotHandler):
        def process(self, callback):   # sync（SDK 线程池跑）；立即 ACK 由 raw_process 负责
            try:
                incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
            except Exception:
                logger.exception("钉钉消息解析失败 data=%s", callback.data)
                return
            text = (incoming.text.content or "").strip() if incoming.text else ""
            if not text:
                return   # 非文本（图片等）MVP 忽略
            staff_id = incoming.sender_staff_id or ""
            chat_type = "p2p" if incoming.conversation_type == "1" else "group"   # 归一（B-P2-3）
            if chat_type == "group" and " " in text and text.startswith("@"):
                text = text.split(" ", 1)[1].strip()   # 群聊 @机器人名 前缀 strip
            if not staff_id or not text:
                return

            def _reply(t: str) -> bool:
                return self.reply_text(t[:3500], incoming)   # 截断留平台侧（B-P2-2）

            try:
                handle_incoming("dingtalk", bid, staff_id, text, _reply, chat_type)
            except Exception:
                logger.exception("钉钉消息处理失败 staffId=%s", staff_id[:10])

    credential = dingtalk_stream.Credential(app_key, app_secret)

    class _Client(dingtalk_stream.DingTalkStreamClient):
        """盲审 A-P0-1 修：open_connection 成功=真"成连"信号（撤看门狗）。原挂 process()
        （用户消息）——网关建连后不推帧（keepalive 是协议级 ping 不进 async for），
        300s 无人发消息的健康 bot 会被杀→pool 退避→永久震荡。"""
        def open_connection(self):
            conn = super().open_connection()
            if conn:
                first_frame.set()
            return conn

    client = _Client(credential)
    client.register_callback_handler(dingtalk_stream.chatbot.ChatbotMessage.TOPIC, _Handler())

    from src.im_bot.users import backfill_from_env
    backfill_from_env(bid)   # 与飞书 ws_client 同构：启动回填（arch-19 双轨收尾语义）
    logger.info("钉钉 Stream runner 起: bot=%s app_key=%s…", bid, app_key[:8])
    client.start_forever()   # 阻塞（SDK 自带断线重连）


if __name__ == "__main__":
    main()
