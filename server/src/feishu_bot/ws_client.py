"""飞书长连接客户端（lark.ws.Client 常驻进程）。

从 DB feishu_config 读凭证，维持 WebSocket 长连接接收消息（不需要公网 webhook）。
systemd quant-feishu-bot@quant.service 管理（auto_reconnect=True 自动重连）。

启动：python -m src.feishu_bot.ws_client
"""
from __future__ import annotations
import json
import logging
import lark_oapi as lark
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from src.data_platform.db import get_conn
from src.quant_common.crypto import decrypt
from src.feishu_bot.bot import process_message_async

logger = logging.getLogger("feishu_bot")
_FID = None  # 当前机器人 id（main 设置，on_message 用）


def load_feishu_credentials(fid=None) -> tuple[str, str]:
    """从 DB 读飞书配置。fid 指定机器人 id，None 读最新 enabled（兼容）。"""
    with get_conn() as conn:
        if fid:
            cur = conn.execute("SELECT app_id, app_secret_encrypted FROM feishu_config WHERE id=%s", (fid,))
        else:
            cur = conn.execute("SELECT app_id, app_secret_encrypted FROM feishu_config WHERE enabled=true ORDER BY id DESC LIMIT 1")
        r = cur.fetchone()
    if not r:
        raise RuntimeError("未配置飞书机器人（feishu_config 无记录），请先扫码接入")
    return r[0], decrypt(r[1])


def on_message(data) -> None:
    """处理收到的消息事件 -> process_message_async（复用 bot.py）。"""
    print(f"=== on_message TRIGGERED: {data}", flush=True)
    logger.info(f"on_message triggered: {data}")
    try:
        event = data.event
        msg = event.message
        open_id = event.sender.sender_id.open_id
        content = msg.content or "{}"
        text = json.loads(content).get("text", "")
        chat_id = getattr(msg, "chat_id", "")
        receive_id = chat_id or open_id
        receive_id_type = "chat_id" if chat_id else "open_id"
        process_message_async(open_id, text, receive_id_type, receive_id, _FID)
    except Exception as e:
        print(f"=== on_message ERROR: {e}", flush=True)
        import traceback; traceback.print_exc()
        logger.error(f"处理飞书消息失败: {e}")


def main() -> None:
    import sys, logging
    global _FID
    _FID = sys.argv[1] if len(sys.argv) > 1 else None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    """启动长连接客户端（阻塞）。"""
    app_id, app_secret = load_feishu_credentials(_FID)
    event_handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG,
        auto_reconnect=True,
    )
    logger.info(f"飞书长连接启动: id={_FID} app_id={app_id}")
    client.start()  # 阻塞维持连接


if __name__ == "__main__":
    main()
