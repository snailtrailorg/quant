"""Web 后端 · IM 机器人路由（批13 五轮：admin 全局面删除——IM 全面用户化，admin 组端点退役。
保留：providers 注册表（告警设置/前端共用）+ /api/my/* 自助组（admin 也是用户，自助面建 bot）。"""

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


# ——— 批11C：自助面（require_authenticated——自定义组零权限用户也能管自己的 IM 通道） ———
# 五轮：IM 全面用户化后这是唯一管理面（admin 也是用户）
# 批12A：向导标识 → onboarding 入口白名单（去 celery——web 进程线程跑；分层：本层引 feishu_bot 层4 合法）
def _wizard_entry(wizard: str):
    if wizard == "feishu_register":
        from src.feishu_bot.tasks import run_onboarding
        return run_onboarding
    return None


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

    频控（批26-5）：per-user 索引键 im:onboarding:owner:{uid} 单键查——键活（非终态）即 429 带
    existing_ticket；终态由 _set_session 即时 DEL。配额：每用户 bot ≤5 + 全平台 ≤10（批26-9）。"""
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
    # 配额：每用户 bot ≤5（11C 挂账量级依据收口——每 bot 子进程 ≈62MB）；
    # 批26-9：+全平台上限 10（三面闸之二——扫码建成的 bot 同样计入 spawn 面）
    with get_conn() as conn:
        n = conn.execute("SELECT count(*) FROM im_bot_config WHERE owner_user_id=%s", (uid,)).fetchone()[0]
        if n >= 5:
            raise ApiError(400, "BOT_QUOTA", "每用户最多 5 个 IM 通道")
        total = conn.execute("SELECT count(*) FROM im_bot_config WHERE enabled").fetchone()[0]
        if total >= 10:
            raise ApiError(400, "BOT_PLATFORM_LIMIT", "平台 IM 通道总数已达上限（10），请先停用不用的通道")
    # 频控（批26-5）：per-user 索引键 O(1) 单键查——替代全键 scan（O(全部活 session)）+
    # scan 与写 ticket 非原子的 TOCTOU（并发双活 ticket）。终态由 _set_session 即时 DEL
    # （语义等价批12A"done 后立即可再发起"）；崩溃未 DEL 兜底=TTL 尽（≤900s）。
    r = feishu_redis_client()
    raw = r.get(f"im:onboarding:owner:{uid}")
    if raw:
        try:
            d = json.loads(raw)
        except Exception:
            d = None   # 坏值=对齐旧 scan 版 fail-open（解析失败放行）；DEL 防坏键挡住新 pending 的 NX
            try:
                r.delete(f"im:onboarding:owner:{uid}")
            except Exception:
                pass
        if d and d.get("status") not in ("done", "error"):
            raise ApiError(429, "ONBOARDING_BUSY", "已有进行中的接入会话，已为你恢复",
                           extra={"existing_ticket": d.get("ticket")})
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
        # 盲审 A-P1-2：pending NX 失败=并发先到 ticket（两请求都过 GET miss 窗口）——必须 429，
        # 静默继续会让第二 ticket 与先到并跑、其中态覆盖写劫持 owner 键、终态 DEL 互删（频控失效窗）
        if not _set_session(session_id, {"status": "pending"}, expire=900, owner_user_id=uid):
            release_onboarding_slot()
            _d = r.get(f"im:onboarding:owner:{uid}")
            _prior = None
            try:
                _prior = json.loads(_d).get("ticket") if _d else None
            except Exception:
                pass
            raise ApiError(429, "ONBOARDING_BUSY", "已有进行中的接入会话，已为你恢复",
                           extra={"existing_ticket": _prior})
        _th.Thread(target=entry, daemon=True,
                   args=(session_id, uid), kwargs={"on_qr": _on_qr, "on_error": _on_error}).start()
    except ApiError:
        raise
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
    key = f"feishu:session:{ticket}"
    data = r.get(key)
    if not data:
        return {"status": "expired"}   # 批12A（A-P2-6）：key 不存在=过期/TTL 尽——pending 混同过期收口
    try:
        d = json.loads(data)
    except Exception:
        return {"status": "expired"}   # 批27-12：坏值按过期收口（fail-open，对齐 :103-110 onboarding 入口先例）
    if d.get("owner_user_id") is not None and d.get("owner_user_id") != int(payload["sub"]):
        raise ApiError(404, "NOT_FOUND", "会话不存在")
    ttl = r.ttl(key)   # 七轮：剩余秒数真值——前端倒计时校准（原恢复路径硬编码 10 分钟与 SDK expire_in≈1h 打架）
    if isinstance(ttl, (int, float)) and ttl > 0:
        d["ttl"] = int(ttl)
    return d


@router.get("/api/my/im-bots")
def my_im_bots(payload: dict = Depends(require_authenticated)):
    """我的 IM 通道列表（owner=会话用户）。含待绑定留痕数（首见 open_id 提示绑定）。"""
    uid = int(payload["sub"])
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, provider, name, description, enabled, updated_at FROM im_bot_config "
            "WHERE owner_user_id=%s ORDER BY id", (uid,)).fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "description": r[3],
             "enabled": r[4], "updated_at": str(r[5])[:19]}
            for r in rows]   # 五轮：pending_binds 退役（绑定取消）


def _platform_bot_count() -> int:
    """批26-9：全平台活 bot 计数（三面闸单源：创建/扫码/启动）——
    1.8G 机器每 bot 子进程 ≈62MB（批B #0d 量级依据），总量护栏（上限 10，用户裁定）。
    并发 TOCTOU 声明：计数与 INSERT 间无事务锁，极端并发可超限 1-2 个——自助面低并发场景接受。"""
    with get_conn() as conn:
        return conn.execute("SELECT count(*) FROM im_bot_config WHERE enabled").fetchone()[0]


@router.post("/api/my/im-bots")
def my_im_bots_create(req: IMBotCreateReq, payload: dict = Depends(require_authenticated)):
    """自助创建（owner=会话用户钉死；default_role 服务端恒 viewer；唯一性预检同管理面 A-P1-5）。"""
    from src.im_bot.base import get_im_provider
    from src.im_bot.credentials import save_bot_credentials   # noqa: F401（与创建语义对齐说明）
    p = get_im_provider(req.provider)
    if p is None:
        raise ApiError(400, "PROVIDER_INVALID", f"未知 IM 平台: {req.provider}")
    # 批26-3：平台×方式一致性闸——注册表无 manual 方式的平台（现即飞书=扫码唯一）API 层拒 form 直建
    #（前端早已只渲染扫码页签，此处堵 API 绕过；扫码向导端点自身已校验 method kind，im_bots.py:81-82）
    if not any(m.get("kind") == "manual" for m in p.ONBOARDING_METHODS.values()):
        raise ApiError(400, "ONBOARDING_INTERACTIVE_ONLY", f"{req.provider} 仅支持扫码接入，请在集成中心使用扫码向导")
    import json as _json
    from src.quant_common.crypto import encrypt as _encrypt
    from src.im_bot.routing import route_key_from
    # 盲审 A-P1-5：手动建同受 ≤5/user 配额（与扫码 onboarding 路径同一条——否则
    # require_authenticated 面可绕配额无限造子进程，≈62MB/bot）。
    # 批26-9：+全平台上限 10（三面闸之一；"建完即启用"故 create 时点即计入）。
    with get_conn() as conn:
        _n = conn.execute("SELECT count(*) FROM im_bot_config WHERE owner_user_id=%s",
                          (int(payload["sub"]),)).fetchone()[0]
        if _n >= 5:
            raise ApiError(400, "BOT_QUOTA", "每用户最多 5 个 IM 通道")
        _total = conn.execute("SELECT count(*) FROM im_bot_config WHERE enabled").fetchone()[0]
        if _total >= 10:
            raise ApiError(400, "BOT_PLATFORM_LIMIT", "平台 IM 通道总数已达上限（10），请先停用不用的通道")
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
            "VALUES (%s,%s,%s,'viewer',true,%s,%s,%s::jsonb) RETURNING id",   # 六轮裁定：建完即启用——凭证建时已过 CREDENTIALS_INCOMPLETE 校验，无需再让用户手动点启动
            (req.provider, req.name, req.description, int(payload["sub"]),
             _encrypt(_json.dumps(req.credentials, ensure_ascii=False)) if has_any else None,
             _json.dumps({"route_key": route})))
        bid = cur.fetchone()[0]
        conn.commit()
    audit_log(payload["username"], "owner_im_bot_create", detail=f"{req.provider}/{req.name}#{bid}")
    return {"id": bid}


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
    bot = _own_bot(bid, payload["sub"])   # 已含 enabled（盲审 A-P2-1：去冗余二次查询）
    # 批26-9：全平台上限 10 三面闸之三（启动面）——只闸创建可被"停旧→建新过闸→再启旧"
    # 确定性绕过（盲审 A-P1-2）；本 bot 已 enabled 时 start 是幂等操作不占新额度，放行
    if not bot["enabled"] and _platform_bot_count() >= 10:
        raise ApiError(400, "BOT_PLATFORM_LIMIT", "平台 IM 通道总数已达上限（10），请先停用不用的通道")
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
