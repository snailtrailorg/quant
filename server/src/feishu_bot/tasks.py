"""飞书扫码接入 Celery 任务（lark.register_app 异步执行）。

register_app 同步阻塞，放 Celery worker 跑（不阻塞 web-api）。
on_qr_code 回调存 Valkey（前端轮询拿二维码）；
成功后凭证加密存 DB im_bot_config(批 2)+ Valkey 存 done 状态。
"""
from __future__ import annotations
import os
import json
import logging
import redis
import lark_oapi as lark

from src.scheduler.app import app as celery_app
from src.data_platform.db import get_conn
from src.quant_common.crypto import encrypt

logger = logging.getLogger("feishu_bot")
VALKEY_URL = os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/4")
_redis = redis.Redis.from_url(VALKEY_URL, decode_responses=True)


_SESSION_OWNER: dict[str, int | None] = {}


def _set_session(session_id: str, data: dict, expire: int = 600) -> None:
    """存扫码会话状态到 Valkey。批11D 盲审 P1-3：自动注入 owner_user_id——
    任何阶段载荷覆盖（scanning/done/error）都不丢归属（频控与 ticket 归属比对的载体）。"""
    data = {**data, "owner_user_id": _SESSION_OWNER.get(session_id)}
    _redis.setex(f"feishu:session:{session_id}", expire, json.dumps(data, ensure_ascii=False))


@celery_app.task(name="src.feishu_bot.tasks.feishu_register_task", bind=True)
def feishu_register_task(self, session_id: str, owner_user_id: int | None = None):
    """调 lark.register_app 扫码创建/连接飞书机器人。

    用户扫码后手机选"连接现有/重新创建"，SDK 返回 client_id/client_secret。
    批11D：owner_user_id——自助面=会话用户（bot 归属钉死）；admin 面=None（平台级）。
    重扫三分支（盲审 A-P1-1+B-P1-1）：他人 bot=error / 平台 bot=只刷凭证不翻 enabled（done 带 owned:false）/ 自己或 admin=现状（翻 enabled+改名）。
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

    # ticket 归属绑定（A-P2-2）：owner 登记+pending 载荷——_set_session 全程自动携带
    _SESSION_OWNER[session_id] = owner_user_id
    _set_session(session_id, {"status": "pending"}, expire=900)

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

        # 批 2(arch-19 v2):存 im_bot_config 统一表——同 app_id 重扫=更新凭证(ON CONFLICT
        # route_key),不再堆重复行(修批 1 审计 A-S1 揭示的旧行为)
        import json as _json
        from src.im_bot.credentials import save_bot_credentials, get_bot_credentials
        def _rescan_verdict(row_owner) -> str:
            """重扫三分支判定（批11D 盲审 A-P1-1+B-P1-1）→ 'error'|'platform'|'own'。"""
            if row_owner is not None and owner_user_id is not None and row_owner != owner_user_id:
                return "error"
            if row_owner is None and owner_user_id is not None:
                return "platform"
            return "own"

        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, credentials_encrypted, owner_user_id FROM im_bot_config "
                "WHERE provider='feishu' AND params->>'route_key'=%s", (app_id,))
            row = cur.fetchone()
            rescan = None
            ins = None
            if not row:
                creds = {"app_id": app_id, "app_secret": app_secret}
                ins = conn.execute(
                    "INSERT INTO im_bot_config (provider, name, default_role, enabled, "
                    "owner_user_id, credentials_encrypted, params) "
                    "VALUES ('feishu', %s, 'viewer', true, %s, %s, %s::jsonb) "
                    "ON CONFLICT (provider, (params->>'route_key')) DO NOTHING "
                    "RETURNING id",
                    (app_name, owner_user_id,
                     encrypt(_json.dumps(creds, ensure_ascii=False)),
                     _json.dumps({"route_key": app_id}))).fetchone()
                conn.commit()
            if ins:
                _audit(owner_user_id, "im_register_create", ins[0], app_id)   # 真插入=新 bot（盲审 P2-1：避开重扫分支误标）
            elif not row:
                # A-P2-6：并发同 app 冲突（INSERT 被吞）→ 重查行走进重扫判定
                cur = conn.execute(
                    "SELECT id, credentials_encrypted, owner_user_id FROM im_bot_config "
                    "WHERE provider='feishu' AND params->>'route_key'=%s", (app_id,))
                row = cur.fetchone()
            if row:
                rescan = _rescan_verdict(row[2])

            if row and rescan == "error":
                # 他人 bot：拒合并（凭证/归属/enabled 全不动）——扫描者明确失败而非静默死胡同
                _set_session(session_id, {"status": "error",
                                          "error": "该应用已被其他账号接入", "code": "ONBOARDING_APP_TAKEN"}, expire=600)
                logger.warning(f"扫码重扫拒: app_id={app_id} 已归属 user={row[2]} 本次 owner={owner_user_id}")
                return

            if row and rescan == "platform":
                # 平台级 bot：只刷新凭证（扫码者持有该 app 飞书侧管理权）——不翻 enabled 不改名
                save_bot_credentials(row[0], {"app_id": app_id, "app_secret": app_secret})
                conn.commit()
                from src.im_bot.feishu_client import evict_feishu_client
                evict_feishu_client(row[0])
                _set_session(session_id, {"status": "done", "app_id": app_id, "owned": False,
                                          "note": "platform_bot"}, expire=600)
                logger.info(f"扫码重扫平台级 bot #{row[0]}：仅刷新凭证（owner 保持 NULL）")
                _audit(owner_user_id, "owner_im_rescan_platform", row[0], app_id)
                return

            if row:
                # 自己/admin 重扫：合并凭证(保已有 token/ek)+翻 enabled+改名（现状语义）
                save_bot_credentials(row[0], {"app_id": app_id, "app_secret": app_secret})
                conn.execute(
                    "UPDATE im_bot_config SET name=%s, enabled=true, updated_at=now() WHERE id=%s",
                    (app_name, row[0]))
                conn.commit()
                _audit(owner_user_id, "im_rescan", row[0], app_id)
        _set_session(session_id, {"status": "done", "app_id": app_id, "owned": True}, expire=600)
        logger.info(f"feishu register done: app_id={app_id}")
        _SESSION_OWNER.pop(session_id, None)
    except Exception as e:
        _set_session(session_id, {"status": "error", "error": str(e)})
        logger.error(f"feishu register 失败: {e}")


def _audit(owner_user_id, action: str, bot_id, app_id: str) -> None:
    """task 侧审计（批11D B-P2-5④）。复用 data_platform.audit_log 正源——
    盲审 P1-2：原 INSERT 列名 username 不存在（表为 actor/target），静默全灭。"""
    try:
        from src.data_platform.audit import audit_log
        who = f"task(owner={owner_user_id})" if owner_user_id else "task(admin)"
        audit_log(who, action, target=str(bot_id) if bot_id else app_id,
                  detail=f"bot={bot_id} app_id={app_id}")
    except Exception as e:
        logger.warning(f"task audit 失败(不影响流程): {e}")
