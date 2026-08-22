"""Web 后端 · IM 机器人路由（/api/im-bots/*，从 main.py 迁出）。"""

from fastapi import APIRouter, Depends, Request, Body, BackgroundTasks
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (IMBotCreateReq, IMBotUpdateReq, IMBotUserReq)
from src.data_platform.db import get_conn
from ..redis_pool import feishu_redis_client
import logging
import json, uuid, subprocess

logger = logging.getLogger("web_api")

router = APIRouter(tags=["im_bots"])


@router.get("/api/im-bots/providers")
def im_bots_providers(payload: dict = Depends(require_perm("im_bots_config"))):
    """平台注册表+动态字段 schema(前端配置表单数据源;单一真相源在后端 FIELD_SCHEMA)。"""
    from src.im_bot.base import list_providers
    return list_providers()


@router.get("/api/im-bots")
def im_bots_list(payload: dict = Depends(require_perm("im_bots_config"))):
    """列全部 IM 机器人(跨平台)。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, provider, name, description, default_role, lang, enabled, priority, "
            "params->>'route_key' AS route_key, updated_at FROM im_bot_config ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "description": r[3],
             "default_role": r[4], "lang": r[5], "enabled": r[6], "priority": r[7],
             "route_key": r[8], "updated_at": str(r[9])} for r in rows]


@router.post("/api/im-bots")
def im_bots_create(req: IMBotCreateReq, payload: dict = Depends(require_perm("im_bots_config"))):
    """手动添加机器人(manual 接入路径;interactive 平台走下方 onboarding)。"""
    from src.im_bot.base import get_im_provider
    from src.im_bot.credentials import save_bot_credentials
    p = get_im_provider(req.provider)
    if p is None:
        raise ApiError(400, "PROVIDER_INVALID", f"未知 IM 平台: {req.provider}")
    if req.default_role not in ("viewer", "analyst", "trader", "admin"):
        raise ApiError(400, "ROLE_INVALID", f"非法角色: {req.default_role}")
    import json as _json
    from src.quant_common.crypto import encrypt as _encrypt
    route = req.credentials.get("app_id") or req.credentials.get("client_id") or \
            req.credentials.get("corp_id") or ""
    has_any = any(v for v in req.credentials.values())
    # A-G1: 同 (provider, route_key) 预检(撞唯一索引裸 500→错误码化;两个空 route_key 也撞)
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT 1 FROM im_bot_config WHERE provider=%s AND params->>'route_key'=%s",
            (req.provider, route))
        if cur.fetchone():
            raise ApiError(400, "BOT_DUPLICATE", f"同 {req.provider} 已有 route_key={route or '(空)'} 的机器人")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO im_bot_config (provider, name, description, default_role, enabled, "
            "credentials_encrypted, params) VALUES (%s,%s,%s,%s,false,%s,%s::jsonb) RETURNING id",
            (req.provider, req.name, req.description, req.default_role,
             # B-S2 修复:原漏 encrypt——明文落密文列且 get_bot_credentials 解密必炸(静默死 bot)
             _encrypt(_json.dumps(req.credentials, ensure_ascii=False)) if has_any else None,
             _json.dumps({"route_key": route})))
        bid = cur.fetchone()[0]
        conn.commit()
    audit_log(payload["username"], "im_bot_create", detail=f"{req.provider}/{req.name}#{bid}")
    return {"id": bid}


@router.post("/api/im-bots/onboarding/{provider}")
def im_bots_onboarding(provider: str, payload: dict = Depends(require_perm("im_bots_config"))):
    """启动辅助接入(扫码/回跳)。飞书=FeishuRegisterTask 扫码(向后兼容原 /api/feishu/connect)。"""
    from src.im_bot.base import get_im_provider
    p = get_im_provider(provider)
    if p is None:
        raise ApiError(404, "PROVIDER_INVALID", f"未知 IM 平台: {provider}")
    if p.ONBOARDING != "interactive":
        raise ApiError(400, "NOT_INTERACTIVE", f"{provider} 走手动添加(manual)")
    if provider == "feishu":
        from src.feishu_bot.tasks import feishu_register_task
        session_id = str(uuid.uuid4())
        feishu_register_task.delay(session_id)
        return {"type": "qr", "ticket": session_id}
    raise ApiError(400, "NOT_IMPLEMENTED", f"{provider} 辅助接入待实现")


@router.get("/api/im-bots/onboarding-status/{ticket}")
def im_bots_onboarding_status(ticket: str, payload: dict = Depends(require_perm("im_bots_config"))):
    """轮询接入状态(通用状态机:pending/scanning/done/error;飞书 Valkey feishu:session)。"""
    r = feishu_redis_client()
    data = r.get(f"feishu:session:{ticket}")
    if not data:
        return {"status": "pending"}
    return json.loads(data)


@router.post("/api/im-bots/{bid}/start")
def im_bots_start(bid: int, payload: dict = Depends(require_perm("im_bots_config"))):
    """启动机器人(hybrid/websocket 型启 systemd 长连接单元;纯 webhook 型只翻 enabled)。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s", (bid,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "BOT_NOT_FOUND", f"机器人 {bid} 不存在")
    from src.im_bot.base import get_im_provider
    p = get_im_provider(row[0])
    if p and p.MODE in ("websocket", "hybrid"):
        # 现状单元名 quant-feishu-bot@{fid}(批 3 多平台再参数化单元名)
        try:
            subprocess.run(["systemctl", "start", f"quant-feishu-bot@{bid}"], check=True, timeout=10)
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": f"systemctl 失败（polkit?）: {e}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    with get_conn() as conn:
        conn.execute("UPDATE im_bot_config SET enabled=true, updated_at=now() WHERE id=%s", (bid,))
        conn.commit()
    audit_log(payload["username"], "im_bot_start", detail=f"#{bid}")
    return {"ok": True}


@router.post("/api/im-bots/{bid}/stop")
def im_bots_stop(bid: int, payload: dict = Depends(require_perm("im_bots_config"))):
    """停止机器人。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s", (bid,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "BOT_NOT_FOUND", f"机器人 {bid} 不存在")
    from src.im_bot.base import get_im_provider
    p = get_im_provider(row[0])
    if p and p.MODE in ("websocket", "hybrid"):
        try:
            subprocess.run(["systemctl", "stop", f"quant-feishu-bot@{bid}"], check=True, timeout=10)
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": f"systemctl 失败: {e}"}   # A-G9:失败不置 enabled(状态归真)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    with get_conn() as conn:
        conn.execute("UPDATE im_bot_config SET enabled=false, updated_at=now() WHERE id=%s", (bid,))
        conn.commit()
    audit_log(payload["username"], "im_bot_stop", detail=f"#{bid}")
    return {"ok": True}


@router.post("/api/im-bots/{bid}")
def im_bots_update(bid: int, req: IMBotUpdateReq,
                   payload: dict = Depends(require_perm("im_bots_config"))):
    """改机器人配置(名称/默认角色/备注/语言/凭证补录)。凭证走 partial 合并。"""
    from src.im_bot.credentials import save_bot_credentials
    if req.default_role and req.default_role not in ("viewer", "analyst", "trader", "admin"):
        raise ApiError(400, "ROLE_INVALID", f"非法角色: {req.default_role}")
    with get_conn() as conn:
        if req.name is not None:
            conn.execute("UPDATE im_bot_config SET name=%s, updated_at=now() WHERE id=%s", (req.name, bid))
        if req.description is not None:
            conn.execute("UPDATE im_bot_config SET description=%s, updated_at=now() WHERE id=%s", (req.description, bid))
        if req.default_role is not None:
            conn.execute("UPDATE im_bot_config SET default_role=%s, updated_at=now() WHERE id=%s", (req.default_role, bid))
        if req.lang is not None:
            conn.execute("UPDATE im_bot_config SET lang=%s, updated_at=now() WHERE id=%s", (req.lang, bid))
        conn.commit()
    if req.credentials:
        if not save_bot_credentials(bid, req.credentials, partial=True):
            raise ApiError(500, "SAVE_FAILED", "凭证写入失败")
    audit_log(payload["username"], "im_bot_update", detail=f"#{bid}")
    return {"ok": True}


@router.delete("/api/im-bots/{bid}")
def im_bots_delete(bid: int, payload: dict = Depends(require_perm("im_bots_config"))):
    """删除机器人(级联删 im_bot_users)。"""
    try:
        subprocess.run(["systemctl", "stop", f"quant-feishu-bot@{bid}"], check=False, timeout=10)
    except Exception:
        pass
    with get_conn() as conn:
        conn.execute("DELETE FROM im_bot_config WHERE id=%s", (bid,))
        conn.commit()
    audit_log(payload["username"], "im_bot_delete", detail=f"#{bid}")
    return {"ok": True}


@router.post("/api/im-bots/{bid}/test")
def im_bots_test(bid: int, payload: dict = Depends(require_perm("im_bots_config"))):
    """测连接(委托 Provider.test_connection——凭证读新表)。"""
    from src.im_bot.base import get_im_provider
    with get_conn() as conn:
        cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s", (bid,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "BOT_NOT_FOUND", f"机器人 {bid} 不存在")
    p = get_im_provider(row[0])
    ok, detail = p.test_connection(bid)
    return {"ok": ok, "detail": detail}


@router.get("/api/im-bots/{bid}/users")
def im_bots_users(bid: int, payload: dict = Depends(require_perm("im_bots_config"))):
    from src.im_bot.users import list_users
    return list_users(bid)


@router.post("/api/im-bots/{bid}/users")
def im_bots_user_upsert(bid: int, req: IMBotUserReq,
                        payload: dict = Depends(require_perm("im_bots_config"))):
    from src.im_bot.users import upsert_user
    r = upsert_user(bid, req.im_user_id, req.role)
    if not r.get("ok"):
        raise ApiError(400, "ROLE_INVALID", r.get("error", "参数无效"))
    audit_log(payload["username"], "im_bot_user_upsert", detail=f"#{bid} {req.im_user_id}:{req.role}")
    return r


@router.delete("/api/im-bots/{bid}/users/{im_user_id}")
def im_bots_user_delete(bid: int, im_user_id: str,
                        payload: dict = Depends(require_perm("im_bots_config"))):
    from src.im_bot.users import delete_user
    delete_user(bid, im_user_id)
    audit_log(payload["username"], "im_bot_user_delete", detail=f"#{bid} {im_user_id}")
    return {"ok": True}