"""飞书长连接客户端（lark.ws.Client 常驻进程）。

从 DB im_bot_config 读凭证(批 2)，维持 WebSocket 长连接接收消息（不需要公网 webhook）。
systemd quant-feishu-bot@quant.service 管理（auto_reconnect=True 自动重连）。

启动：python -m src.feishu_bot.ws_client
"""
from __future__ import annotations
import json
import logging
import lark_oapi as lark
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from src.data_platform.db import get_conn
from src.feishu_bot.bot import process_message_async

logger = logging.getLogger("feishu_bot")
_FID = None  # 当前机器人 id（main 设置，on_message 用）


def load_feishu_credentials(fid=None) -> tuple[str, str]:
    """从 im_bot_config 读飞书凭证(批 2,19 号 v2)。
    fid 指定机器人 id，None 读最新 enabled（兼容）。"""
    from src.im_bot.credentials import get_bot_credentials
    creds = get_bot_credentials(fid) if fid else {}
    if not creds:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id FROM im_bot_config WHERE provider='feishu' AND enabled "
                "ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        if row:
            creds = get_bot_credentials(row[0])
    if not creds.get("app_id") or not creds.get("app_secret"):
        raise RuntimeError("未配置飞书机器人（im_bot_config 无有效凭证），请先扫码接入或在 Web 补录")
    return creds["app_id"], creds["app_secret"]


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
        # 补审E-3：起后台线程处理（与 router/webhook 路径对齐）——原同步跑在 lark ws 的
        # asyncio 事件循环上，LLM chat 阻塞期间 ping 停发可能被服务端断连
        import threading
        threading.Thread(target=process_message_async, daemon=True,
                         args=(open_id, text, receive_id_type, receive_id, _FID)).start()
    except Exception as e:
        print(f"=== on_message ERROR: {e}", flush=True)
        import traceback; traceback.print_exc()
        logger.error(f"处理飞书消息失败: {e}")


def main() -> None:
    import sys   # 顶部导入（函数后段残留旧 import sys 会把 sys 变局部——12:03 prod feishu 波崩溃根因）
    # 补审E-8：单元实例名须为数字 bot id（quant-feishu-bot@{bid}）；非数字 fail-fast——
    # 原静默降级会让 _FID 污染流入 SQL DataError→首见整段死火回到零留痕盲区
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        raise SystemExit("用法: python -m src.feishu_bot.ws_client <bot_id>（数字——systemd 实例名）")
    # staging 彩排假 bot（2026-09-03 彩排盲区改进）：system_config feishu_mock_ws=true 时
    # 跳过 Lark 长连接直接驻留——staging 波次能重启到本单元、跑满 main() 启动路径
    # （argv 校验/导入，即 12:03 prod E-8 崩溃所在），不真连外网。prod 无此键=真实连接。
    # 检查失败 fail-open 走真实路径（DB 不可达时整个彩排本就会挂，无需在此自锁）。
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            _mock = conn.execute(
                "SELECT value FROM system_config WHERE key='feishu_mock_ws'").fetchone()
        if _mock and str(_mock[0]).strip().lower() in ("1", "true", "yes"):
            print(f"feishu mock 模式: bot={sys.argv[1]} 驻留（不连 Lark）——staging 彩排假 bot", flush=True)
            import threading
            threading.Event().wait()  # 阻塞驻留，systemd 保持 running（dwell 通过）
            return
    except Exception:
        pass
    # 2026-09-02：启动即回填（19 号双轨收尾——env 授权用户入表，告警 dispatch 同源可用）
    from src.im_bot.users import backfill_from_env
    backfill_from_env(int(sys.argv[1]))
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
