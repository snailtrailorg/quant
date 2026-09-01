"""认证/用户/日志路由 —— 从 main.py 提取的 auth/user/log 端点。

启动: 由 main.py include_router 挂载。
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Header, Query, Body, Request, BackgroundTasks, HTTPException
from ..auth import (
    create_jwt, authenticate, create_user, require_role, require_perm,
    audit_log, ensure_default_admin, init_users_table, PERMISSIONS,
    invite_user, register_user, forgot_password, reset_password, change_password, verify_token,
    validate_password, guard_user_mutation, soft_delete_user, guard_self_deactivate,
)
from ..errors import ApiError
from ..models import (LoginReq, UserCreate, StrategyConfig, InviteReq, RegisterReq, ForgotReq, ResetReq, ChangePwdReq, LogAnalyzeReq, ChatReq, LLMModelReq, IMBotCreateReq, IMBotUpdateReq, IMBotUserReq, LlmBudgetReq, DataSourceReq, ChannelReq, BrokerReq, RiskRuleReq, PoolReq, StrategyAccountReq)
from src.data_platform.db import get_conn
from src.email_service import send_invite_email, send_activation_email, send_password_reset_email
import logging
import os
from pathlib import Path as _Path

logger = logging.getLogger("web_api")

router = APIRouter(tags=["auth_routes"])

# ——— 头像静态服务目录（同 main.py 保持一致） ———
# 2026-08-26 3b 修正：运行时数据位=shared 层（与 main.py 同源同默认；版本树内逐版丢失且 quant 无权写）。
# 开发机回退：shared 位不存在（无 /data）时用代码树相对位，与 main.py 回退链同构。
_AVATAR_DIR = _Path(os.environ.get("AVATAR_DIR",
                                   "/data/websites/snailtrail.cc/quant/shared/static/avatars"))
# 2026-08-27 双盲审 P1-1：Path.is_dir() 遇 EACCES 会 raise（非返回 False）——staging 建成后
# 开发机 import 即崩（test_log_analyze 实锤）。对齐 main.py 回退机制：except OSError 判回退。
try:
    _avatar_ok = _AVATAR_DIR.is_dir()
except OSError:
    _avatar_ok = False
if not _avatar_ok:
    _AVATAR_DIR = _Path(__file__).resolve().parents[3] / "static" / "avatars"
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)

# P4 轻量限流（审计 B-服务层 OWASP API4）：内存滑窗（单进程足够——部署单 uvicorn worker），
# login 10 次/分/IP（防爆破）、forgot 3 次/分/IP（防邮件轰炸）。重启清零可接受。
_RATE_LIMITS: dict[str, dict[str, list[float]]] = {}
_RATE_RULES = {"login": (10, 60), "forgot": (3, 60)}


def _rate_limited(bucket: str, key: str) -> bool:
    import time as _t
    limit, window = _RATE_RULES[bucket]
    now = _t.time()
    store = _RATE_LIMITS.setdefault(bucket, {}).setdefault(key, [])
    store[:] = [ts for ts in store if now - ts < window]
    if len(store) >= limit:
        return True
    store.append(now)
    return False


@router.post("/api/auth/login")
def login(req: LoginReq, request: Request):
    if _rate_limited("login", request.client.host if request.client else "?"):
        raise ApiError(429, "RATE_LIMITED", "尝试过于频繁，请稍后再试")
    user = authenticate(req.username, req.password)  # 支持 用户名 或 邮箱（含 @）
    if not user:
        raise ApiError(401, "INVALID_CREDENTIALS", "用户名或密码错误")
    token = create_jwt(str(user["id"]), user["username"], user["role"])
    # A3: 记录上次登录（时间 + IP，X-Forwarded-For 取真实来源）
    client_ip = (request.headers.get("x-forwarded-for") or request.client.host or "")[:45]
    try:
        with get_conn() as conn:
            conn.execute("UPDATE users SET last_login_at=now(), last_login_ip=%s WHERE id=%s",
                         (client_ip, user["id"]))
            conn.commit()
    except Exception as e:
        print(f"[login] last_login update failed (ignored): {e}", flush=True)
    audit_log(user["username"], "login", detail=client_ip)
    return {"token": token, "role": user["role"], "username": user["username"]}


@router.get("/api/auth/me")
def me(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    nickname, avatar_url, db_role = payload["username"], None, None
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT nickname, avatar_url, role FROM users WHERE id=%s", (payload["sub"],))
            r = cur.fetchone()
        if r:
            nickname, avatar_url, db_role = r[0] or payload["username"], r[1], r[2]
    except Exception:
        pass
    # P3-7（10 §7 差距 6）：role 以 DB 为准——修 JWT 24h 不刷新（改角色即时生效）；
    # permissions 同步换查表真源
    from ..auth import load_effective_permissions
    role = db_role or payload["role"]
    perms, sources = load_effective_permissions(payload["username"], role)
    # W4 玻璃盒:来源标注（role-base/user-override）+被 user deny 的键;不含 updated_by（盲审 A-P2）
    denied = sources.pop("__denied__", [])
    return {"user_id": payload["sub"], "username": payload["username"], "role": role,
            "nickname": nickname, "avatar_url": avatar_url,
            "permissions": sorted(perms), "perm_sources": sources, "denied": denied}


# W4（10 §4）：nav 16 项/数据域清单——后端单源常量,前端从 GET 拿（不硬编码第二份）
NAV_ITEMS = [
    {"id": "dashboard", "group": "base"},
    {"id": "screener", "group": "research"}, {"id": "pool", "group": "research"},
    {"id": "factors", "group": "research"}, {"id": "strategy", "group": "research"},
    {"id": "backtest", "group": "research"}, {"id": "analysis", "group": "research"},
    {"id": "live-task", "group": "live"}, {"id": "trading", "group": "live"},
    {"id": "risk", "group": "riskgrp"}, {"id": "reconcile", "group": "riskgrp"},
    {"id": "risk-rules", "group": "riskgrp"},
    {"id": "dataops", "group": "ops"}, {"id": "integrations", "group": "ops"},
    {"id": "observe", "group": "ops"}, {"id": "settings", "group": "ops"},
]
DATA_FIELDS = {"markets": ["astock", "convertible", "etf", "crypto"],
               "sensitivity": ["detail", "aggregated", "count"]}


def _load_dim(dimension: str) -> dict:
    """角色→{resource: effect}（nav/data 维;行存在即显性配置）。"""
    from src.data_platform.db import get_conn as _gc
    try:
        with _gc() as conn:
            rows = conn.execute(
                "SELECT subject_id, resource, effect FROM permission "
                "WHERE subject_type='role' AND dimension=%s", (dimension,)).fetchall()
        out: dict = {}
        for sid, res, eff in rows:
            out.setdefault(sid, {})[res] = eff
        return out
    except Exception:
        return {}


@router.get("/api/permissions")
def get_permissions(payload: dict = Depends(require_role("admin"))):
    """W4 三维矩阵（10 §4）：api 键+nav 三态+数据域+user override 全景。"""
    from ..auth import load_role_permissions
    from src.data_platform.db import get_conn as _gc
    all_keys = ["read", "strategy_control", "data_sync", "halt", "resume", "trade",
                "live_trading_control", "risk_rules", "account_keys", "user_mgmt",
                "system_config", "llm_config", "im_bots_config"]
    roles = load_role_permissions()
    overrides = []
    try:
        with _gc() as conn:
            rows = conn.execute(
                "SELECT subject_id, dimension, resource, effect FROM permission "
                "WHERE subject_type='user'").fetchall()
        overrides = [{"username": r[0], "dimension": r[1], "resource": r[2], "effect": r[3]}
                     for r in rows]
    except Exception:
        pass
    return {"keys": all_keys,
            "roles": {r: sorted(roles.get(r, set())) for r in ("viewer", "analyst", "trader", "admin")},
            "nav": {"items": NAV_ITEMS, "roles": _load_dim("nav")},
            "data": {"fields": DATA_FIELDS, "roles": _load_dim("data")},
            "user_overrides": overrides}


@router.post("/api/permissions/{role}")
def update_permissions(role: str, body: dict, dimension: str = "api",
                       payload: dict = Depends(require_role("admin"))):
    """改角色权限集。W4：dimension ∈ api|nav|data（缺省 api 兼容旧前端）。

    api 维=全量重写 allow 集；nav/data 维=全量重写 {resource: effect} 映射。
    锁键（W4 盲审 B-P0 新建——原"系统策略键已锁定"是幻觉）：LOCKED_PERM_KEYS
    双路径同锁——角色重写自动地板保护（请求集被静默校正,锁键恒保持现值）;
    admin 角色另加 ADMIN_ROLE_FLOOR（self-lockout 防线）。返回 preserved 提示校正。
    """
    from ..auth import invalidate_perm_cache, load_role_permissions, LOCKED_PERM_KEYS, ADMIN_ROLE_FLOOR
    from src.data_platform.db import get_conn as _gc
    if role not in ("viewer", "analyst", "trader", "admin"):
        raise HTTPException(400, "BAD_ROLE")
    if dimension not in ("api", "nav", "data"):
        raise HTTPException(400, "BAD_DIMENSION", "dimension ∈ api|nav|data")
    if dimension == "api":
        keys = set(body.get("permissions", []) or [])
        if not keys:
            # 终审 A-P2-11：空集会让 load 回退字典=全撤权失效（空集歧义）
            raise HTTPException(400, "EMPTY_PERMISSIONS", "权限集不可为空（至少保留 read）")
        current = set(load_role_permissions().get(role, set()))
        # 盲审 A-P1c 修：admin 地板=锁键+system_config（原 &LOCKED 把 FLOOR 的
        # system_config 截成死代码——admin 重写可去 system_config=自锁防线失真）
        if role == "admin":
            floor_keys = LOCKED_PERM_KEYS | {"system_config"}
            preserved = (current | ADMIN_ROLE_FLOOR) & floor_keys
        else:
            floor_keys = LOCKED_PERM_KEYS
            preserved = current & LOCKED_PERM_KEYS
        keys = (keys - floor_keys) | preserved            # 地板键恒保持现值（双路径同锁之一）
        out = sorted(keys)
        with _gc() as conn:
            conn.execute("DELETE FROM permission WHERE subject_type='role' AND subject_id=%s "
                         "AND dimension='api'", (role,))
            for k in out:
                conn.execute(
                    "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, updated_by) "
                    "VALUES ('role', %s, 'api', %s, 'allow', %s)",
                    (role, k, payload.get("username", "")))
            conn.commit()
        invalidate_perm_cache()
        return {"role": role, "permissions": out,
                "preserved_locked": sorted(preserved & (set(body.get("permissions", [])) ^ preserved))}
    # nav/data 维：body.resources = {resource: effect}
    res_map = body.get("resources", {}) or {}
    valid_res = {i["id"] for i in NAV_ITEMS} if dimension == "nav" \
        else (set(DATA_FIELDS["markets"]) | set(DATA_FIELDS["sensitivity"]))
    bad = set(res_map) - valid_res
    if bad:
        raise HTTPException(400, "BAD_RESOURCE", f"未知资源: {sorted(bad)}")
    if dimension == "nav":
        bad_eff = {v for v in res_map.values()} - {"hidden", "readonly", "readwrite"}
    else:
        bad_eff = {v for v in res_map.values()} - {"allow", "deny"}
    if bad_eff:
        raise HTTPException(400, "BAD_EFFECT", f"非法 effect: {sorted(bad_eff)}")
    with _gc() as conn:
        conn.execute("DELETE FROM permission WHERE subject_type='role' AND subject_id=%s "
                     "AND dimension=%s", (role, dimension))
        for res, eff in res_map.items():
            conn.execute(
                "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, updated_by) "
                "VALUES ('role', %s, %s, %s, %s, %s)",
                (role, dimension, res, eff, payload.get("username", "")))
        conn.commit()
    invalidate_perm_cache()
    return {"role": role, "dimension": dimension, "resources": res_map}


@router.post("/api/permissions/user/{username}")
def update_user_override(username: str, body: dict,
                         payload: dict = Depends(require_role("admin"))):
    """W4 C 阶段：per-user override（10 §4 用户视图=角色+override）。

    body: {dimension ∈ api|nav|data, resource, effect ∈ allow|deny|clear}
    - clear=删该行（回到角色基线）
    - 锁键（api 维 LOCKED_PERM_KEYS）双路径同锁 → 400 PERMISSION_KEY_LOCKED
    - 自锁防线：目标用户是 admin 时拒 deny 其管理键（self-lockout,盲审 B-P1）
    subject_id=username（W4 定死——0056 注释 user_id 弃,盲审 A/B-P1）。
    """
    from ..auth import invalidate_perm_cache, LOCKED_PERM_KEYS
    from src.data_platform.db import get_conn as _gc
    dimension = body.get("dimension", "api")
    resource = body.get("resource", "")
    effect = body.get("effect", "")
    if dimension not in ("api", "nav", "data"):
        raise HTTPException(400, "BAD_DIMENSION")
    if effect not in ("allow", "deny", "clear"):
        raise HTTPException(400, "BAD_EFFECT")
    if not resource:
        raise HTTPException(400, "BAD_RESOURCE", "resource 必填")
    if dimension == "api":
        if resource in LOCKED_PERM_KEYS:
            raise HTTPException(400, "PERMISSION_KEY_LOCKED",
                                f"{resource} 为系统策略锁键（双路径同锁,不可 override）")
        # 自锁防线：目标用户是 admin 时,deny 其余管理键也拒（锁死后无 UI 恢复路径）
        try:
            with _gc() as conn:
                trole = conn.execute("SELECT role FROM users WHERE username=%s",
                                     (username,)).fetchone()
            if trole and trole[0] == "admin" and effect == "deny" \
                    and resource in ("system_config", "user_mgmt"):
                raise HTTPException(400, "SELF_LOCK_RISK",
                                    f"拒绝对 admin 用户 deny {resource}（自锁防线）")
        except HTTPException:
            raise
        except Exception:
            pass   # users 表不可读时放行校验（DB 写入本身也会失败兜底）
    with _gc() as conn:
        conn.execute("DELETE FROM permission WHERE subject_type='user' AND subject_id=%s "
                     "AND dimension=%s AND resource=%s", (username, dimension, resource))
        if effect != "clear":
            conn.execute(
                "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, updated_by) "
                "VALUES ('user', %s, %s, %s, %s, %s)",
                (username, dimension, resource, effect, payload.get("username", "")))
        conn.commit()
    invalidate_perm_cache()
    audit_log(payload.get("username", ""), "perm_override",
              f"{username}:{dimension}:{resource}:{effect}")
    return {"username": username, "dimension": dimension, "resource": resource, "effect": effect}


@router.post("/api/auth/logout")
def logout(authorization: str = Header(...),
           payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """登出：token 加入黑名单立即失效（A4）+ 审计。"""
    from ..auth import revoke_jwt
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    revoked = revoke_jwt(token)
    audit_log(payload["username"], "logout", detail=f"revoked={revoked}")
    return {"ok": True, "revoked": revoked}


# ——— 邀请制用户管理 ———

def _request_base(request: Request) -> str:
    """从请求推导 base_url（scheme://host），用 X-Forwarded-* 避免 nginx 后 scheme 错成 http。"""
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")


@router.get("/api/user/profile")
def profile_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """个人中心：当前用户资料（批次C）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT username, nickname, role, avatar_url, email FROM users WHERE id=%s", (payload["sub"],))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
    return {"username": r[0], "nickname": r[1], "role": r[2], "avatar_url": r[3], "email": r[4]}


@router.post("/api/user/profile")
def profile_update_api(body: dict = Body(...),
                       payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """更新昵称（批次C；仅昵称可自助改，角色/用户名只读）。"""
    nickname = str(body.get("nickname", "")).strip()[:20]
    if not nickname:
        raise ApiError(400, "NICKNAME_REQUIRED", "昵称不能为空")
    with get_conn() as conn:
        conn.execute("UPDATE users SET nickname=%s WHERE id=%s", (nickname, payload["sub"]))
        conn.commit()
    audit_log(payload["username"], "update_profile", f"nickname={nickname}")
    return {"ok": True}


@router.post("/api/user/avatar")
def avatar_upload_api(body: dict = Body(...),
                      payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """设置头像（批次C+）：{icon:"icon_NN.png"} 选系统卡通图标（public/icons，36 个）
    或 {avatar_base64} 上传（裁剪 1:1 → Pillow 统一 256px JPEG 存 static/avatars，
    固定文件名 user_{id}.jpg 覆盖旧图无孤儿，URL 带 ?t= 防缓存）。"""
    import base64 as _b64
    # 1) 系统图标：仅允许 icon_NN.png（0-35），防路径注入
    icon = str(body.get("icon", "") or "").strip()
    if icon:
        import re as _re
        if not _re.fullmatch(r"icon_(?:[0-2]\d|3[0-5])\.png", icon):
            raise ApiError(400, "AVATAR_INVALID", "无效的系统图标")
        url = f"/icons/{icon}"
        with get_conn() as conn:
            conn.execute("UPDATE users SET avatar_url=%s, avatar_updated_at=now() WHERE id=%s",
                         (url, payload["sub"]))
            conn.commit()
        audit_log(payload["username"], "avatar_icon", icon)
        return {"avatar_url": url}
    import io as _io
    import time as _time
    data = str(body.get("avatar_base64", ""))
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        raw = _b64.b64decode(data)
    except Exception:
        raise ApiError(400, "AVATAR_INVALID", "头像数据无效")
    if len(raw) > 2 * 1024 * 1024:
        raise ApiError(400, "AVATAR_TOO_LARGE", "图片不能超过 2MB")
    try:
        from PIL import Image
        img = Image.open(_io.BytesIO(raw))
        img.load()
        if img.format not in ("JPEG", "PNG", "WEBP"):
            raise ApiError(400, "AVATAR_FORMAT", "仅支持 JPG、PNG、WebP 格式")
        # 中心方形裁剪（裁剪器已 1:1，此处兜底）→ RGB → 256px
        w, h = img.size
        side = min(w, h)
        img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = img.resize((256, 256), Image.LANCZOS)
        fname = f"user_{payload['sub']}.jpg"
        img.save(_AVATAR_DIR / fname, "JPEG", quality=85)
    except ApiError:
        raise
    except Exception:
        raise ApiError(400, "AVATAR_INVALID", "图片解析失败")
    url = f"/api/static/avatars/{fname}?t={int(_time.time())}"
    with get_conn() as conn:
        conn.execute("UPDATE users SET avatar_url=%s, avatar_updated_at=now() WHERE id=%s",
                     (url, payload["sub"]))
        conn.commit()
    audit_log(payload["username"], "avatar_upload")
    return {"avatar_url": url}


@router.post("/api/user/deactivate")
def deactivate_api(authorization: str = Header(...),
                   payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """自助注销（批次D）：软删+脱敏+token 拉黑。末位 admin 不可注销自己（该路径真实可达）。"""
    guard_self_deactivate(int(payload["sub"]))
    soft_delete_user(int(payload["sub"]))
    from ..auth import revoke_jwt
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    revoke_jwt(token)
    _af = _AVATAR_DIR / f"user_{payload['sub']}.jpg"
    if _af.exists():
        _af.unlink()
    audit_log(payload["username"], "self_deactivate")
    return {"ok": True}


@router.get("/api/invites")
def invites_api(payload: dict = Depends(require_perm("user_mgmt"))):
    """邀请记录列表（批次B 可观测）：待注册/已用/已过期/已撤销。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, email, expires_at, used, revoked, created_at "
            "FROM user_tokens WHERE type='invite' ORDER BY id DESC LIMIT 200")
        rows = cur.fetchall()
    from datetime import datetime as _dt
    now = _dt.now()
    items = []
    for r in rows:
        status = ("revoked" if r[4] else "used" if r[3]
                  else "expired" if (r[2] and str(r[2]) < str(now)) else "pending")
        items.append({"id": r[0], "email": r[1],
                      "expires_at": str(r[2])[:19] if r[2] else None,
                      "status": status, "created_at": str(r[5])[:19]})
    return {"items": items}


@router.post("/api/invites/{tid}/revoke")
def invite_revoke_api(tid: int, payload: dict = Depends(require_perm("user_mgmt"))):
    """撤销邀请（仅未使用的可撤；批次B）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE user_tokens SET revoked=true WHERE id=%s AND type='invite' AND used=false AND revoked=false RETURNING id",
            (tid,))
        r = cur.fetchone()
        conn.commit()
    if not r:
        raise ApiError(400, "INVITE_NOT_REVOKABLE", "仅未使用的邀请可撤销")
    audit_log(payload["username"], "invite_revoke", str(tid))
    return {"ok": True}


@router.post("/api/auth/invite")
async def invite_user_api(req: InviteReq, request: Request, background_tasks: BackgroundTasks,
                          payload: dict = Depends(require_perm("user_mgmt"))):
    """admin 邀请：填 email 发邀请邮件（默认 Viewer）。邮件后台发送，接口立即返回（SMTP 慢不阻塞）。"""
    email = (req.email or "").strip()
    # 轻量格式校验：防手滑（如 hotmailcom 少点直接被 SMTP 拒）
    domain = email.split("@")[-1] if "@" in email else ""
    if "@" not in email or "." not in domain:
        raise ApiError(400, "EMAIL_INVALID_FORMAT", "邮箱格式无效（检查是否漏了 . 或 @）")
    token = invite_user(email)
    if not token:
        raise ApiError(400, "EMAIL_REGISTERED", "该邮箱已注册（如需重发邀请，请先删除该账号或换邮箱）")
    background_tasks.add_task(send_invite_email, email, token, _request_base(request), req.lang)
    audit_log(payload["username"], "invite_user", email)
    return {"status": "invited", "email": email}


@router.get("/api/auth/invite/verify")
def verify_invite_token(token: str):
    """验证 invite token 有效性（前端开通页用）。"""
    t = verify_token(token, "invite")
    if not t:
        raise ApiError(400, "TOKEN_INVALID_OR_EXPIRED", "token 无效或已过期")
    return {"valid": True, "email": t["email"]}


@router.post("/api/auth/register")
async def register_api(req: RegisterReq, request: Request, background_tasks: BackgroundTasks):
    """自助开通：凭 invite token 建用户（默认 Viewer）。"""
    validate_password(req.password)  # 不达标直接抛 ApiError(含错误码)
    user = register_user(req.token, req.username, req.password)
    if not user:
        raise ApiError(400, "TOKEN_OR_USERNAME_INVALID", "token 无效/已用/过期，或用户名已存在")
    audit_log(user["username"], "self_register")
    # 开通通知邮件后台发送（带条款，内容大发送慢，不阻塞注册响应）
    background_tasks.add_task(send_activation_email, user["email"], user["username"], _request_base(request), req.lang)
    return {"status": "registered", "username": user["username"], "email": user["email"]}


@router.post("/api/auth/forgot-password")
async def forgot_password_api(req: ForgotReq, request: Request, background_tasks: BackgroundTasks):
    """找回密码：发重置邮件（后台发送，SMTP 慢/失败不阻塞接口；不泄露 email 是否存在）。"""
    if _rate_limited("forgot", request.client.host if request.client else "?"):   # P4：防邮件轰炸
        raise ApiError(429, "RATE_LIMITED", "请求过于频繁，请稍后再试")
    token = forgot_password(req.email)
    if not token:
        return {"status": "sent"}  # email 不存在也返回 sent（防枚举）
    background_tasks.add_task(send_password_reset_email, req.email, token, _request_base(request), req.lang)
    return {"status": "sent"}


@router.post("/api/auth/reset-password")
def reset_password_api(req: ResetReq):
    """凭 reset token 重置密码。"""
    validate_password(req.new_password)  # 不达标直接抛 ApiError(含错误码)
    ok = reset_password(req.token, req.new_password)
    if not ok:
        raise ApiError(400, "TOKEN_INVALID_OR_EXPIRED", "token 无效或已用/过期")
    return {"status": "reset"}


@router.post("/api/auth/change-password")
def change_password_api(req: ChangePwdReq, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """改密码：需旧密码验证。"""
    validate_password(req.new_password)  # 不达标直接抛 ApiError(含错误码)
    ok = change_password(int(payload["sub"]), req.old_password, req.new_password)
    if not ok:
        raise ApiError(400, "OLD_PASSWORD_WRONG", "旧密码错误")
    audit_log(payload["username"], "change_password")
    return {"status": "changed"}


# ——— 用户管理（Admin） ———

@router.post("/api/user")
def create_user_api(req: UserCreate, payload: dict = Depends(require_perm("user_mgmt"))):
    validate_password(req.password)   # P0-复审残留：admin 建用户原无校验（>72 字节 500）
    if req.role not in ("admin", "trader", "analyst", "viewer"):   # P2 A4：角色白名单
        raise ApiError(400, "ROLE_INVALID", f"非法角色: {req.role}")
    try:
        uid = create_user(req.username, req.password, req.role)
        audit_log(payload["username"], "create_user", req.username, f"role={req.role}")
        return {"id": uid, "username": req.username, "role": req.role}
    except ValueError as e:
        raise ApiError(409, "USERNAME_EXISTS", str(e))


@router.get("/api/user")
def list_users(payload: dict = Depends(require_perm("user_mgmt"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, username, nickname, role, enabled, email, email_verified, created_at, "
                           "last_login_at, deleted_at FROM users ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "username": r[1], "nickname": r[2], "role": r[3],
             "enabled": r[4] and not r[9], "deactivated": bool(r[9]),
             "email": r[5], "email_verified": r[6], "created_at": str(r[7])[:19],
             "last_login_at": str(r[8])[:19] if r[8] else None} for r in rows]


@router.post("/api/user/{uid}")
def update_user(uid: int, role: str = None, enabled: bool = None,
                payload: dict = Depends(require_perm("user_mgmt"))):
    """改用户角色/禁用。保护：不能动自己（末位 admin 由 user_mgmt=admin-only + 不动自己 隐式保证）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT username FROM users WHERE id=%s", (uid,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
    guard_user_mutation(row[0], payload["username"])
    with get_conn() as conn:
        if role is not None:
            conn.execute("UPDATE users SET role=%s WHERE id=%s", (role, uid))
        if enabled is not None:
            conn.execute("UPDATE users SET enabled=%s WHERE id=%s", (enabled, uid))
        conn.commit()
    audit_log(payload["username"], "update_user", str(uid), f"role={role} enabled={enabled}")
    return {"ok": True}


@router.delete("/api/user/{uid}")
def delete_user(uid: int, payload: dict = Depends(require_perm("user_mgmt"))):
    """删除用户。保护：不能动自己（末位 admin 由 user_mgmt=admin-only + 不动自己 隐式保证）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT username FROM users WHERE id=%s", (uid,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
    guard_user_mutation(row[0], payload["username"])
    soft_delete_user(uid)  # 批次D：软删+脱敏（审计/关联数据保留）
    # 清头像文件
    import os as _os
    _af = _AVATAR_DIR / f"user_{uid}.jpg"
    if _af.exists():
        _af.unlink()
    audit_log(payload["username"], "delete_user", str(uid))
    return {"ok": True}


# ——— 日志 ———

def _analyze_logs_with_llm(logs: list[dict]) -> str:
    """LLM 归因异常日志，返回分析文本（D4 #34）。caller=log_analyze。"""
    if not logs:
        return "无异常日志"
    from src.llm_gateway import gateway
    log_text = "\n".join(
        f"[{l.get('level','')}] {l.get('module') or l.get('step_name') or ''}: {l.get('msg') or l.get('message','')}"
        for l in logs
    )
    try:
        resp = gateway.chat(
            messages=[
                {"role": "system", "content": "你是运维归因助手，分析异常日志的根因并给出排查建议，用中文回复"},
                {"role": "user", "content": f"以下是异常日志，请分析可能原因并给出排查建议：\n{log_text}"},
            ],
            role="viewer",
            caller="log_analyze",
        )
        return resp.content if resp and resp.content else "（LLM 无响应，请检查 API key）"
    except Exception as e:
        return f"（LLM 暂不可用: {e}）"


@router.post("/api/log/analyze")
def log_analyze(req: LogAnalyzeReq, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """AI 日志归因：传 logs 或 task_id，LLM 分析根因（D4 #34）。"""
    logs = []
    if req.logs:
        logs = req.logs
    elif req.task_id:
        from src.task_manager import get_task
        task = get_task(req.task_id)
        if not task:
            raise HTTPException(404, f"任务 {req.task_id} 不存在")
        logs = task.get("logs", [])
    # 过滤 ERROR/WARN（INFO/DEBUG 不归因）
    logs = [l for l in logs if l.get("level", "").upper() in ("ERROR", "WARN")]
    analysis = _analyze_logs_with_llm(logs)
    return {"analysis": analysis, "log_count": len(logs), "logs": logs}


@router.get("/api/log")
def get_logs(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """运行日志（P3-1 接 task_logs 真实日志，不再占位）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM task_logs LIMIT 1")
        except Exception:
            logger.warning("get_logs: task_logs 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute(
            "SELECT level, message, step_name, created_at FROM task_logs "
            "ORDER BY created_at DESC LIMIT 200")
        rows = cur.fetchall()
    return {"logs": [{"level": r[0], "msg": r[1], "module": r[2] or "",
                      "ts": str(r[3])[:19] if r[3] else ""} for r in rows]}