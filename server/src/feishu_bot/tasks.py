"""飞书扫码接入 Celery 任务（lark.register_app 异步执行）。

register_app 同步阻塞，放 Celery worker 跑（不阻塞 web-api）。
on_qr_code 回调存 Valkey（前端轮询拿二维码）；
成功后凭证加密存 DB feishu_config + Valkey 存 done 状态。
"""
from __future__ import annotations
import os
import json
import logging
import redis
import lark_oapi as lark

from src.scheduler.app import app as celery_app
from src.data_platform.db import get_conn
from src.web_api.crypto_utils import encrypt

logger = logging.getLogger("feishu_bot")
VALKEY_URL = os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/4")


def _set_session(session_id: str, data: dict, expire: int = 600) -> None:
    """存扫码会话状态到 Valkey。"""
    r = redis.Redis.from_url(VALKEY_URL, decode_responses=True)
    r.setex(f"feishu:session:{session_id}", expire, json.dumps(data, ensure_ascii=False))


@celery_app.task(name="src.feishu_bot.tasks.feishu_register_task", bind=True)
def feishu_register_task(self, session_id: str):
    """调 lark.register_app 扫码创建/连接飞书机器人。

    用户扫码后手机选"连接现有/重新创建"，SDK 返回 client_id/client_secret。
    """
    app_preset = {
        "name": "量化交易助手",
        "desc": "多市场量化交易平台飞书机器人",
    }

    def on_qr_code(info):
        # info 含 url（二维码内容）+ expire_in；生成 base64 二维码图片供前端渲染
        import qrcode, io, base64
        url = info.get("url", "")
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        _set_session(session_id, {
            "status": "scanning",
            "qr_url": url,
            "qr_img": qr_b64,
            "expire_in": info.get("expire_in", 600),
        }, expire=info.get("expire_in", 600))

    # 默认权限（addons）：发消息 + 收消息事件 + 卡片回调
    addons = {
        "scopes": {"tenant": ["im:message:send_as_bot"]},
        "events": {"items": {"tenant": ["im.message.receive_v1"]}},
        "callbacks": {"items": ["card.action.trigger"]},
    }
    try:
        result = lark.register_app(
            on_qr_code=on_qr_code,
            on_status_change=lambda info: logger.info(f"feishu register status: {info}"),
            app_preset=app_preset,
            addons=addons,
        )
        # 成功：result 含 client_id/client_secret（SDK 返回 dict 或对象，兼容两种）
        if hasattr(result, "get"):
            app_id = result.get("client_id", "")
            app_secret = result.get("client_secret", "")
        else:
            app_id = getattr(result, "client_id", "")
            app_secret = getattr(result, "client_secret", "")

        if not app_id:
            _set_session(session_id, {"status": "error", "error": "register_app 未返回 app_id"})
            return

        # 获取应用名称（调飞书 API）
        import httpx
        app_name = ""
        try:
            token_resp = httpx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
            token = token_resp.json().get("tenant_access_token", "")
            if token:
                # /bot/v3/info 获取机器人信息（机器人自己查自己，不需应用管理权限）
                app_resp = httpx.get("https://open.feishu.cn/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"}, timeout=10)
                bot_data = app_resp.json()
                logger.info(f"bot/v3/info 返回: {bot_data}")
                app_name = bot_data.get("bot", {}).get("app_name", "")
        except Exception as e:
            logger.warning(f"获取飞书应用名称失败: {e}")

        # 加密存 DB feishu_config（含 name）
        enc_secret = encrypt(app_secret) if app_secret else ""
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO feishu_config (name, app_id, app_secret_encrypted, role, enabled) VALUES (%s,%s,%s,%s,true)",
                (app_name, app_id, enc_secret, "viewer"))
            conn.commit()

        _set_session(session_id, {"status": "done", "app_id": app_id}, expire=600)
        logger.info(f"feishu register done: app_id={app_id}")
    except Exception as e:
        _set_session(session_id, {"status": "error", "error": str(e)})
        logger.error(f"feishu register 失败: {e}")
