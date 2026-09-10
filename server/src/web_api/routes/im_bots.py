"""Web 后端 · IM 机器人路由（/api/im-bots/*，从 main.py 迁出）。"""

from fastapi import APIRouter, Depends, Request, Body, BackgroundTasks
from fastapi.responses import JSONResponse
from ..auth import require_role, require_perm, require_authenticated, audit_log
from ..errors import ApiError
from ..models import (IMBotCreateReq, IMBotUpdateReq, IMBotUserReq)
from src.data_platform.db import get_conn
from ..redis_pool import feishu_redis_client
import logging
import json, uuid

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
            "SELECT b.id, b.provider, b.name, b.description, b.default_role, b.lang, b.enabled, b.priority, "
            "b.params->>'route_key' AS route_key, b.updated_at, b.owner_user_id, u.username "
            "FROM im_bot_config b LEFT JOIN users u ON u.id=b.owner_user_id ORDER BY b.id")
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "description": r[3],
             "default_role": r[4], "lang": r[5], "enabled": r[6], "priority": r[7],
             "route_key": r[8], "updated_at": str(r[9]),
             "owner_user_id": r[10], "owner": r[11] or "platform"} for r in rows]   # 批11C：归属展示


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
    from src.im_bot.routing import route_key_from
    route = route_key_from(req.credentials)
    has_any = any(v for v in req.credentials.values())
    # A-P2-8（批13）：空凭证/身份键缺失拦截；manual-only 平台（钉钉/企微）secret 全必填。
    # 飞书豁免 secret 全检——admin 手填 verification_token/encrypt_key 可空（历史合法形态）。
    if not has_any or not route or (p.ONBOARDING == "manual"
                                    and any(not req.credentials.get(f) for f in p.required_fields)):
        raise ApiError(400, "CREDENTIALS_INCOMPLETE", "凭证不完整（必填字段缺失）")
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
    """启动辅助接入（扫码/回跳）。批12A：飞书=web 进程线程跑 run_onboarding（去 celery）。"""
    from src.im_bot.base import get_im_provider
    p = get_im_provider(provider)
    if p is None:
        raise ApiError(404, "PROVIDER_INVALID", f"未知 IM 平台: {provider}")
    if p.ONBOARDING != "interactive":
        raise ApiError(400, "NOT_INTERACTIVE", f"{provider} 走手动添加(manual)")
    if provider == "feishu":
        from src.feishu_bot.tasks import run_onboarding, acquire_onboarding_slot, release_onboarding_slot, _set_session
        if not acquire_onboarding_slot():
            raise ApiError(429, "ONBOARDING_BUSY", "当前接入人数较多，请稍后再试")
        session_id = str(uuid.uuid4())
        import threading as _th
        try:   # 盲审 A-P1-2：同自助面——acquire 后失败归还帽位
            _set_session(session_id, {"status": "pending"}, expire=900, owner_user_id=None)   # admin=平台级
            _th.Thread(target=run_onboarding, daemon=True, args=(session_id,)).start()
        except Exception:
            release_onboarding_slot()
            raise
        return {"type": "qr", "ticket": session_id}
    raise ApiError(400, "NOT_IMPLEMENTED", f"{provider} 辅助接入待实现")


@router.get("/api/im-bots/onboarding-status/{ticket}")
def im_bots_onboarding_status(ticket: str, payload: dict = Depends(require_perm("im_bots_config"))):
    """轮询接入状态(通用状态机:pending/scanning/done/error;飞书 Valkey feishu:session)。"""
    r = feishu_redis_client()
    data = r.get(f"feishu:session:{ticket}")
    if not data:
        return {"status": "expired"}   # 批12A（A-P2-7）：与自助面语义对齐（原 pending 混同过期）
    d = json.loads(data)
    d.pop("bind_code", None)   # 批11E（B-P2）：admin 面不泄露他人自助会话的验证码
    return d


def _guard_start_credentials(bid: int, provider: str) -> None:
    """批13（盲审 B-P1-1）：start 前校验凭证齐备——防空/残凭证 bot 进 pool 退避循环
    （存量 bot 批13 前建库无校验，start 时是最后一道闸）。manual-only 平台 secret 全检，
    interactive 平台（飞书）校验身份键（route_key）。"""
    from src.im_bot.base import get_im_provider
    from src.im_bot.credentials import get_bot_credentials
    from src.im_bot.routing import route_key_from
    p = get_im_provider(provider)
    if p is None:
        return   # 未注册实现：pool 自然 skip（前向兼容），不拦 start
    creds = get_bot_credentials(bid)
    if not creds or not route_key_from(creds) or \
       (p.ONBOARDING == "manual" and any(not creds.get(f) for f in p.required_fields)):
        raise ApiError(400, "CREDENTIALS_INCOMPLETE", "凭证不完整（先补录凭证再启动）")


@router.post("/api/im-bots/{bid}/start")
def im_bots_start(bid: int, payload: dict = Depends(require_perm("im_bots_config"))):
    """启动机器人(hybrid/websocket 型启 systemd 长连接单元;纯 webhook 型只翻 enabled)。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s", (bid,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "BOT_NOT_FOUND", f"机器人 {bid} 不存在")
    _guard_start_credentials(bid, row[0])
    # 批11C：pool 化后 start=翻 enabled 开关（quant-im-pool 30s 对账拉起子进程；systemctl 装拆退役）
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
    # 批11C：pool 化后 stop=翻 enabled 开关（pool 30s 内 terminate 子进程）
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
    if p is None:
        raise ApiError(400, "PROVIDER_INVALID", f"平台 {row[0]} 未注册实现")
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
    r = upsert_user(bid, req.im_user_id, req.role, user_id=req.user_id)
    if not r.get("ok"):
        raise ApiError(400, "ROLE_INVALID", r.get("error", "参数无效"))
    audit_log(payload["username"], "im_bot_user_upsert", detail=f"#{bid} {req.im_user_id}:{req.role} user={req.user_id}")
    return r


@router.delete("/api/im-bots/{bid}/users/{im_user_id}")
def im_bots_user_delete(bid: int, im_user_id: str,
                        payload: dict = Depends(require_perm("im_bots_config"))):
    from src.im_bot.users import delete_user
    delete_user(bid, im_user_id)
    audit_log(payload["username"], "im_bot_user_delete", detail=f"#{bid} {im_user_id}")
    return {"ok": True}

# ——— 批11C：自助面（require_authenticated——自定义组零权限用户也能管自己的 IM 通道） ———
# 批12A：向导标识 → onboarding 入口白名单（去 celery——web 进程线程跑；分层：本层引 feishu_bot 层4 合法）
def _wizard_entry(wizard: str):
    if wizard == "feishu_register":
        from src.feishu_bot.tasks import run_onboarding
        return run_onboarding
    return None
# 安全锚点（方案 v2 双盲审）：owner_user_id 服务端钉死=认证会话；default_role 恒 viewer（展示用，
# 非权限身份——身份源=绑定）；body 的 owner/default_role/user_id 一律忽略；IDOR=owner 守卫。

def _own_bot(bid: int, user_id) -> dict:
    """取自己的 bot（owner 守卫——他人/平台级 bot 一律 404 防探测）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, provider, name, description, enabled FROM im_bot_config "
            "WHERE id=%s AND owner_user_id=%s", (bid, user_id))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "BOT_NOT_FOUND", "机器人不存在或不属于你")
    return {"id": r[0], "provider": r[1], "name": r[2], "description": r[3], "enabled": r[4]}


@router.get("/api/my/im-bots/providers")
def my_im_providers(payload: dict = Depends(require_authenticated)):
    """自助面 provider 注册表+FIELD_SCHEMA（与 admin 面同源——表单元数据无秘密，A-P2-2）。"""
    from src.im_bot.base import list_providers
    return list_providers()


@router.post("/api/my/im-bots/onboarding/{provider}/{method}")
def my_im_onboarding(provider: str, method: str, payload: dict = Depends(require_authenticated)):
    """自助扫码向导（批12A 去 celery）：注册表校验+频控+配额 → web 线程 run_onboarding（owner=会话钉死）。

    频控：每用户同时 1 个活 ticket（session 载荷 owner 比对+非终态）；配额：每用户 bot ≤5。"""
    from src.im_bot.base import get_im_provider
    p = get_im_provider(provider)
    if p is None:
        raise ApiError(404, "PROVIDER_INVALID", f"未知 IM 平台: {provider}")
    m = p.ONBOARDING_METHODS.get(method)
    if not m or m.get("kind") != "interactive":
        raise ApiError(400, "METHOD_NOT_INTERACTIVE", f"方式 {method} 不存在或不支持扫码向导")
    entry = _wizard_entry(m.get("wizard", ""))
    if entry is None:
        raise ApiError(400, "WIZARD_UNKNOWN", f"向导未注册: {m.get('wizard')}")
    uid = int(payload["sub"])
    # 配额：每用户 bot ≤5（11C 挂账量级依据收口——每 bot 子进程 ≈62MB）
    with get_conn() as conn:
        n = conn.execute("SELECT count(*) FROM im_bot_config WHERE owner_user_id=%s", (uid,)).fetchone()[0]
        if n >= 5:
            raise ApiError(400, "BOT_QUOTA", "每用户最多 5 个 IM 通道")
    # 频控：活 ticket 扫描（owner 比对且非终态）——命中则带 existing_ticket 给前端恢复活会话（批12A #8）
    r = feishu_redis_client()
    for key in r.scan_iter("feishu:session:*", count=100):
        try:
            d = json.loads(r.get(key) or "{}")
            if d.get("owner_user_id") == uid and d.get("status") not in ("done", "error"):
                raise ApiError(429, "ONBOARDING_BUSY", "已有进行中的接入会话，已为你恢复",
                               extra={"existing_ticket": str(key).rsplit(":", 1)[-1]})
        except ApiError:
            raise
        except Exception:
            continue
    # 全局并发帽（批12A A-P1-1②：SDK 无 timeout 的 daemon 线程总闸——满则 429）
    from src.feishu_bot.tasks import acquire_onboarding_slot, release_onboarding_slot
    if not acquire_onboarding_slot():
        raise ApiError(429, "ONBOARDING_BUSY", "当前接入人数较多，请稍后再试")
    # 盲审 A-P1-2：acquire 后基础设施失败（Valkey 瞬断/线程起不来）必须归还帽位——
    # 否则 8 次后全站 onboarding 429 直到重启
    session_id = str(uuid.uuid4())
    import threading as _th
    qr_evt = _th.Event()
    err_evt = _th.Event()   # 快失败短路（A-P2-4：init 异常不等满 5s）
    holder = {}
    def _on_qr(info):
        holder.update(info); qr_evt.set()
    def _on_error(msg):
        holder["error"] = msg; err_evt.set()
    try:
        from src.feishu_bot.tasks import _set_session
        _set_session(session_id, {"status": "pending"}, expire=900, owner_user_id=uid)   # TOCTOU 收口（A-P2-1）
        _th.Thread(target=entry, daemon=True,
                   args=(session_id, uid), kwargs={"on_qr": _on_qr, "on_error": _on_error}).start()
    except Exception:
        release_onboarding_slot()
        raise
    audit_log(payload["username"], "owner_im_onboarding_start", detail=f"{provider}/{method} ticket={session_id[:8]}…")
    # 同步等出码（≤5s——正常 1~2s；失败即返；超时 202 前端回落轮询）
    import time as _t
    deadline = _t.monotonic() + 5.0
    while _t.monotonic() < deadline and not qr_evt.is_set() and not err_evt.is_set():
        qr_evt.wait(0.2)
    if err_evt.is_set():
        raise ApiError(502, "ONBOARDING_FAILED", holder.get("error", "接入发起失败"))
    if qr_evt.is_set():
        return {"ticket": session_id, **{k: holder[k] for k in ("qr_url", "qr_img", "expire_in") if k in holder}}
    return JSONResponse(status_code=202, content={"ticket": session_id})


@router.get("/api/my/im-bots/onboarding-status/{ticket}")
def my_im_onboarding_status(ticket: str, payload: dict = Depends(require_authenticated)):
    """轮询自助向导状态（ticket 归属绑定：session 载荷 owner≠会话 → 404，A-P2-2）。"""
    r = feishu_redis_client()
    data = r.get(f"feishu:session:{ticket}")
    if not data:
        return {"status": "expired"}   # 批12A（A-P2-6）：key 不存在=过期/TTL 尽——pending 混同过期收口
    d = json.loads(data)
    if d.get("owner_user_id") is not None and d.get("owner_user_id") != int(payload["sub"]):
        raise ApiError(404, "NOT_FOUND", "会话不存在")
    return d   # 自助面原样返回（含 bind_code——owner 本人才能看到码）


@router.get("/api/my/im-bots")
def my_im_bots(payload: dict = Depends(require_authenticated)):
    """我的 IM 通道列表（owner=会话用户）。含待绑定留痕数（首见 open_id 提示绑定）。"""
    uid = int(payload["sub"])
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, provider, name, description, enabled, updated_at FROM im_bot_config "
            "WHERE owner_user_id=%s ORDER BY id", (uid,)).fetchall()
        pending = dict()
        for r in rows:
            cur = conn.execute(
                "SELECT count(*) FROM im_bot_users WHERE bot_id=%s AND user_id IS NULL", (r[0],))
            pending[r[0]] = cur.fetchone()[0]
    return [{"id": r[0], "provider": r[1], "name": r[2], "description": r[3],
             "enabled": r[4], "updated_at": str(r[5])[:19], "pending_binds": pending.get(r[0], 0)}
            for r in rows]


@router.post("/api/my/im-bots")
def my_im_bots_create(req: IMBotCreateReq, payload: dict = Depends(require_authenticated)):
    """自助创建（owner=会话用户钉死；default_role 服务端恒 viewer；唯一性预检同管理面 A-P1-5）。"""
    from src.im_bot.base import get_im_provider
    from src.im_bot.credentials import save_bot_credentials   # noqa: F401（与创建语义对齐说明）
    p = get_im_provider(req.provider)
    if p is None:
        raise ApiError(400, "PROVIDER_INVALID", f"未知 IM 平台: {req.provider}")
    import json as _json
    from src.quant_common.crypto import encrypt as _encrypt
    from src.im_bot.routing import route_key_from
    # 盲审 A-P1-5：手动建同受 ≤5/user 配额（与扫码 onboarding 路径同一条——否则
    # require_authenticated 面可绕配额无限造子进程，≈62MB/bot）
    with get_conn() as conn:
        _n = conn.execute("SELECT count(*) FROM im_bot_config WHERE owner_user_id=%s",
                          (int(payload["sub"]),)).fetchone()[0]
        if _n >= 5:
            raise ApiError(400, "BOT_QUOTA", "每用户最多 5 个 IM 通道")
    route = route_key_from(req.credentials)
    has_any = any(v for v in req.credentials.values())
    # A-P2-8（批13）：自助面同款拦截（manual-only 平台 secret 全必填；飞书无手动建路径）
    if not has_any or not route or (p.ONBOARDING == "manual"
                                    and any(not req.credentials.get(f) for f in p.required_fields)):
        raise ApiError(400, "CREDENTIALS_INCOMPLETE", "凭证不完整（必填字段缺失）")
    with get_conn() as conn:
        if conn.execute(
                "SELECT 1 FROM im_bot_config WHERE provider=%s AND params->>'route_key'=%s",
                (req.provider, route)).fetchone():
            raise ApiError(400, "BOT_DUPLICATE", f"同 {req.provider} 已有 route_key={route or '(空)'} 的机器人")
        cur = conn.execute(
            "INSERT INTO im_bot_config (provider, name, description, default_role, enabled, "
            "owner_user_id, credentials_encrypted, params) "
            "VALUES (%s,%s,%s,'viewer',false,%s,%s,%s::jsonb) RETURNING id",
            (req.provider, req.name, req.description, int(payload["sub"]),
             _encrypt(_json.dumps(req.credentials, ensure_ascii=False)) if has_any else None,
             _json.dumps({"route_key": route})))
        bid = cur.fetchone()[0]
        conn.commit()
    audit_log(payload["username"], "owner_im_bot_create", detail=f"{req.provider}/{req.name}#{bid}")
    return {"id": bid}


@router.post("/api/my/im-bots/{bid}/bind")
def my_im_bind(bid: int, body: dict = Body(...), payload: dict = Depends(require_authenticated)):
    """绑定 IM 身份到**会话用户本人**（user_id 服务端钉死=A-P0-2；open_id 从待绑定列表或拒答消息来）。"""
    from src.im_bot.users import bind_owner
    _own_bot(bid, payload["sub"])   # 只能绑自己 bot 的消息身份
    open_id = str(body.get("open_id", "")).strip()
    if not open_id or len(open_id) > 64:
        raise ApiError(400, "OPEN_ID_INVALID", "open_id 无效")
    bind_owner(bid, open_id, int(payload["sub"]))
    try:
        from src.im_bot.bindcode import revoke
        revoke(bid)   # 方案 §2：手动绑成功同 DEL 码（B-P2-2）
    except Exception:
        pass
    audit_log(payload["username"], "owner_im_bind", detail=f"#{bid} {open_id[:8]}…")
    return {"ok": True}


@router.get("/api/my/im-bots/{bid}/pending")
def my_im_pending(bid: int, payload: dict = Depends(require_authenticated)):
    """待绑定留痕列表（首见 open_id——bootstrap 通路：拒答消息里拿 open_id→此处一键绑定）。"""
    _own_bot(bid, payload["sub"])
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT im_user_id, created_at FROM im_bot_users "
            "WHERE bot_id=%s AND user_id IS NULL ORDER BY id DESC LIMIT 50", (bid,)).fetchall()
    return [{"open_id": r[0], "created_at": str(r[1])[:19]} for r in rows]


@router.post("/api/my/im-bots/{bid}")
def my_im_update(bid: int, req: IMBotUpdateReq, payload: dict = Depends(require_authenticated)):
    """改自己的 bot（名称/描述/凭证补录；default_role/owner 不可动）。"""
    _own_bot(bid, payload["sub"])
    with get_conn() as conn:
        if req.name is not None:
            conn.execute("UPDATE im_bot_config SET name=%s, updated_at=now() WHERE id=%s", (req.name, bid))
        if req.description is not None:
            conn.execute("UPDATE im_bot_config SET description=%s, updated_at=now() WHERE id=%s", (req.description, bid))
        conn.commit()
    if req.credentials:
        from src.im_bot.credentials import save_bot_credentials
        if not save_bot_credentials(bid, req.credentials, partial=True):
            raise ApiError(500, "SAVE_FAILED", "凭证写入失败")
    audit_log(payload["username"], "owner_im_bot_update", detail=f"#{bid}")
    return {"ok": True}


@router.delete("/api/my/im-bots/{bid}")
def my_im_delete(bid: int, payload: dict = Depends(require_authenticated)):
    _own_bot(bid, payload["sub"])
    with get_conn() as conn:
        conn.execute("DELETE FROM im_bot_config WHERE id=%s", (bid,))
        conn.commit()
    audit_log(payload["username"], "owner_im_bot_delete", detail=f"#{bid}")
    return {"ok": True}


@router.post("/api/my/im-bots/{bid}/start")
def my_im_start(bid: int, payload: dict = Depends(require_authenticated)):
    _own_bot(bid, payload["sub"])
    from src.data_platform.db import get_conn as _gc
    with _gc() as conn:
        _pv = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s", (bid,)).fetchone()
    if _pv:
        _guard_start_credentials(bid, _pv[0])   # 批13 B-P1-1：自助面同款启动校验
    with get_conn() as conn:
        conn.execute("UPDATE im_bot_config SET enabled=true, updated_at=now() WHERE id=%s", (bid,))
        conn.commit()
    audit_log(payload["username"], "owner_im_bot_start", detail=f"#{bid}")
    return {"ok": True}


@router.post("/api/my/im-bots/{bid}/stop")
def my_im_stop(bid: int, payload: dict = Depends(require_authenticated)):
    _own_bot(bid, payload["sub"])
    with get_conn() as conn:
        conn.execute("UPDATE im_bot_config SET enabled=false, updated_at=now() WHERE id=%s", (bid,))
        conn.commit()
    audit_log(payload["username"], "owner_im_bot_stop", detail=f"#{bid}")
    return {"ok": True}
