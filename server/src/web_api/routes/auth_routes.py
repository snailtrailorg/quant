"""认证/用户/日志路由 —— 从 main.py 提取的 auth/user/log 端点。

启动: 由 main.py include_router 挂载。
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Header, Query, Body, Request, BackgroundTasks
from ..auth import (
    create_jwt, authenticate, create_user, require_role, require_perm, require_authenticated,
    audit_log, ensure_default_admin, init_users_table, PERMISSIONS,
    invite_user, register_user, forgot_password, reset_password, change_password, verify_token,
    validate_password, guard_user_mutation, soft_delete_user, guard_self_deactivate,
)
from ..errors import ApiError
from ..models import (LoginReq, UserCreate, StrategyConfig, InviteReq, RegisterReq, ForgotReq, ResetReq, ChangePwdReq, ChatReq, LLMModelReq, IMBotCreateReq, IMBotUpdateReq, IMBotUserReq, LlmBudgetReq, DataSourceReq, ChannelReq, BrokerReq, RiskRuleReq, PoolReq, StrategyAccountReq, EmailChangeReq, EmailConfirmReq, PhoneCodeReq, PhoneChangeReq)
from src.data_platform.db import get_conn
from src.email_service import send_invite_email, send_activation_email, send_password_reset_email, send_email_change_email
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
# 开发机 import 即崩（批23 前的 log_analyze 测试实锤）。对齐 main.py 回退机制：except OSError 判回退。
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
_RATE_RULES = {"login": (10, 60), "forgot": (3, 60), "emailchg": (3, 3600),
               "phonechg": (3, 3600)}   # 批20 发信+批30 发码（宽窗——发信有成本）；批30 桶键=uid（反代后 host 共桶坑不复制）

# 批11：注册用户名保留字（防冒充系统身份；大小写不敏感）。
# 批26-8：补"官方客服"（批11A 盲审 P2-11 词表缺口）；比对统一走 _username_reserved
# （NFKC 归一化防全角 ａｄｍｉｎ 绕过）。边界声明：不做前缀模糊匹配（误伤 admin2 类正常名）；
# NFKC 不折叠西里尔等 homoglyph——接受，防冒充主目标=中英文平台身份词。
_RESERVED_USERNAMES = {"admin", "administrator", "root", "system", "support", "官方", "官方客服", "蜗牛量化"}


def _username_reserved(name: str) -> bool:
    """保留字判定（批26-8 单源）：NFKC 归一化 + strip + lower 后精确匹配。注册与 admin 建用户共用。"""
    import unicodedata
    return unicodedata.normalize("NFKC", name or "").strip().lower() in _RESERVED_USERNAMES


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
        logger.warning("login last_login 更新失败（不阻断登录）: %s", e)   # 批27-8：print→logger（可观测）
    audit_log(user["username"], "login", detail=client_ip)
    return {"token": token, "role": user["role"], "username": user["username"]}


@router.get("/api/auth/me")
def me(payload: dict = Depends(require_authenticated)):
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
    # W4 玻璃盒:来源标注（批33a 后恒 role-base,denied 恒空）;不含 updated_by（盲审 A-P2）
    denied = sources.pop("__denied__", [])
    from ..auth import load_nav_map
    return {"user_id": payload["sub"], "username": payload["username"], "role": role,
            "nickname": nickname, "avatar_url": avatar_url,
            "permissions": sorted(perms), "perm_sources": sources, "denied": denied,
            "nav": load_nav_map(payload["username"], role)}


# W4（10 §4）：nav/数据域清单——后端单源常量,前端从 GET 拿（不硬编码第二份）
# 批11B：加 users（用户管理，与 MainLayout 菜单对齐——盲审 P2-7）
NAV_ITEMS = [
    {"id": "dashboard", "group": "base"},
    {"id": "screener", "group": "research"}, {"id": "pool", "group": "research"},
    {"id": "factors", "group": "research"}, {"id": "strategy", "group": "research"},
    {"id": "backtest", "group": "research"}, {"id": "analysis", "group": "research"},
    {"id": "live-task", "group": "live"}, {"id": "trading", "group": "live"},
    {"id": "risk", "group": "riskgrp"}, {"id": "reconcile", "group": "riskgrp"},
    {"id": "risk-rules", "group": "riskgrp"},
    {"id": "users", "group": "ops"},
    {"id": "dataops", "group": "ops"}, {"id": "integrations", "group": "ops"},
    {"id": "observe", "group": "ops"}, {"id": "settings", "group": "ops"},
]


def _all_group_names() -> list[str]:
    """用户组名全量（批11B）；读失败或表空 → 四内置名回退（盲审 B P2-3：空表=未迁移环境跑新代码防静默半瘫）。"""
    from src.data_platform.db import get_conn as _gc
    try:
        with _gc() as conn:
            rows = conn.execute("SELECT name FROM user_group ORDER BY builtin DESC, id").fetchall()
        names = [r[0] for r in rows]
        if names:
            return names
    except Exception:
        pass
    return ["admin", "trader", "analyst", "viewer"]


def _load_dim(dimension: str, strict: bool = False) -> dict:
    """角色→{resource: effect}（nav/market_op 维;行存在即显性配置）。

    strict=True 读失败抛 503（market_op 段用——静默 {} 会让矩阵显示全不勾，
    误保存=全 deny 钉死，代码盲审 B-P2）；缺省容错 {}（nav 维既有语义）。
    """
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
        if strict:
            raise ApiError(503, "PERM_DIM_READ_FAILED",
                           f"权限维度 {dimension} 读取失败——矩阵可能显示不全，请勿在此状态保存")
        return {}


@router.get("/api/permissions")
def get_permissions(payload: dict = Depends(require_perm("user_mgmt"))):
    """W4 三维矩阵（10 §4）：api 键+nav 三态+市场操作权限全景（批33a：user override 维
    退役——权限单源化，用户裁定权限完全追随组）。
    批11B：角色清单动态（user_group 表全量，失败/空回退四内置）+locked 随 GET 返回（前端 🔒 不再硬编码）。"""
    from ..auth import load_role_permissions, LOCKED_PERM_KEYS, _MARKET_OP_KEYS
    all_keys = ["read", "strategy_control", "data_sync", "halt", "resume", "trade",
                "live_trading_control", "risk_rules", "account_keys", "user_mgmt",
                "system_config", "llm_config", "im_bots_config", "alerts_config"]
    roles = load_role_permissions()
    group_names = _all_group_names()   # 恒以组表为准（与权限数据短暂分叉可接受，盲审 B P2-4）
    return {"keys": all_keys,
            "locked": sorted(LOCKED_PERM_KEYS),
            "roles": {r: sorted(roles.get(r, set())) for r in group_names},
            "nav": {"items": NAV_ITEMS, "roles": _load_dim("nav")},
            # 批15：data 维退役（脱敏+markets 存而不灵），换 market_op（市场操作权限）。
            # keys 单源 perms._MARKET_OP_KEYS（防第二份五键清单漂移）；strict=读失败 503
            "market_op": {"keys": list(_MARKET_OP_KEYS), "roles": _load_dim("market_op", strict=True)}}


def _ensure_group(conn, role: str) -> None:
    """组存在性校验（写事务内调用——代码盲审 A-P2-1/B-P2-2：独立连接=TOCTOU 窗口可给已删组写行）。"""
    if not conn.execute("SELECT 1 FROM user_group WHERE name=%s", (role,)).fetchone():
        raise ApiError(400, "GROUP_NOT_FOUND", f"用户组不存在: {role}")


@router.post("/api/permissions/{role}")
def update_permissions(role: str, body: dict, dimension: str = "api",
                       payload: dict = Depends(require_perm("user_mgmt"))):
    """改角色权限集。W4：dimension ∈ api|nav|market_op（缺省 api 兼容旧前端）。

    api 维=全量重写 allow 集；nav/market_op 维=全量重写 {resource: effect} 映射。
    （批15：data 维退役——脱敏删+markets 存而不灵由 market_op 顶替。）
    锁键（W4 盲审 B-P0 新建——原"系统策略键已锁定"是幻觉）：LOCKED_PERM_KEYS
    双路径同锁——角色重写自动地板保护（请求集被静默校正,锁键恒保持现值）;
    admin 角色另加 ADMIN_ROLE_FLOOR（self-lockout 防线）。返回 preserved 提示校正。
    """
    from ..auth import (invalidate_perm_cache, load_role_permissions,
                        LOCKED_PERM_KEYS, ADMIN_ROLE_FLOOR, _MARKET_OP_KEYS)
    from src.data_platform.db import get_conn as _gc
    if dimension not in ("api", "nav", "market_op"):
        raise ApiError(400, "BAD_DIMENSION", "dimension ∈ api|nav|market_op")
    if dimension == "api":
        keys = set(body.get("permissions", []) or [])
        if not keys:
            # 终审 A-P2-11：空集会让 load 回退字典=全撤权失效（空集歧义）
            raise ApiError(400, "EMPTY_PERMISSIONS", "权限集不可为空（至少保留 read）")
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
            _ensure_group(conn, role)   # 写事务内校验（防删组竞态窗口幽灵行——盲审 A-P2-1）
            conn.execute("DELETE FROM permission WHERE subject_type='role' AND subject_id=%s "
                         "AND dimension='api'", (role,))
            for k in out:
                conn.execute(
                    "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, updated_by) "
                    "VALUES ('role', %s, 'api', %s, 'allow', %s)",
                    (role, k, payload.get("username", "")))
            conn.commit()
        invalidate_perm_cache()
        # 批27-22：三维全量重写补审计（原零 audit——谁给哪个组加了什么键不可追溯，对齐 update_user_override 先例）
        audit_log(payload.get("username", ""), "perm_role_update", role,
                  f"api allow={len(out)} 键: {','.join(out[:12])}{'…' if len(out) > 12 else ''}")
        return {"role": role, "permissions": out,
                "preserved_locked": sorted(preserved & (set(body.get("permissions", [])) ^ preserved))}
    # nav/market_op 维：body.resources = {resource: effect}
    res_map = body.get("resources", {}) or {}
    valid_res = ({i["id"] for i in NAV_ITEMS} if dimension == "nav"
                 else set(_MARKET_OP_KEYS))   # 批15：market_op 单源五键（data 维退役）
    bad = set(res_map) - valid_res
    if bad:
        raise ApiError(400, "BAD_RESOURCE", f"未知资源: {sorted(bad)}")
    if dimension == "nav":
        bad_eff = {v for v in res_map.values()} - {"hidden", "readonly", "readwrite"}
    else:
        bad_eff = {v for v in res_map.values()} - {"allow", "deny"}
    if bad_eff:
        raise ApiError(400, "BAD_EFFECT", f"非法 effect: {sorted(bad_eff)}")
    with _gc() as conn:
        _ensure_group(conn, role)   # 写事务内校验（同上）
        conn.execute("DELETE FROM permission WHERE subject_type='role' AND subject_id=%s "
                     "AND dimension=%s", (role, dimension))
        for res, eff in res_map.items():
            conn.execute(
                "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, updated_by) "
                "VALUES ('role', %s, %s, %s, %s, %s)",
                (role, dimension, res, eff, payload.get("username", "")))
        conn.commit()
    invalidate_perm_cache()
    # 批27-22：nav/market_op 维同补审计
    _summary = ",".join(f"{r}={e}" for r, e in sorted(res_map.items())[:10]) or "（空=清空该维）"
    audit_log(payload.get("username", ""), "perm_role_update", role, f"{dimension}: {_summary}")
    return {"role": role, "dimension": dimension, "resources": res_map}


# 批33a：POST /api/permissions/user/{username}（update_user_override）整端点退役——
# user 维随权限单源化废除（用户裁定 2026-09-16：权限完全追随组）；全仓唯一 user 写点
# 随删=SELF_LOCK_RISK 防线退役实为封死唯一绕过面（锁键组层单锁仍在 POST /api/permissions/{role}）。


@router.post("/api/auth/logout")
def logout(authorization: str = Header(...),
           payload: dict = Depends(require_authenticated)):
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
def profile_api(payload: dict = Depends(require_authenticated)):
    """个人中心：当前用户资料（批次C；批20 扩信息台+权限概览）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT username, nickname, role, avatar_url, email, created_at, last_login_at, "
            "last_login_ip, enabled, deleted_at, phone FROM users WHERE id=%s", (payload["sub"],))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
    # 批20 20B：市场操作权限五键（批15 单源逐键；role 用 DB role——JWT 陈旧角色防漂移，盲审A-P2-6）
    market_op = {}
    try:
        from src.data_platform.perms import market_op_allowed, _MARKET_OP_KEYS
        role = payload.get("db_role") or payload.get("role") or r[2]
        for mk in _MARKET_OP_KEYS:   # 单源五键（盲审B-P2-4：防第二份清单漂移）
            market_op[mk] = bool(market_op_allowed(r[0], role, mk))
    except Exception:
        pass   # 权限面故障不挡资料展示（chips 空=前端省略）
    return {"username": r[0], "nickname": r[1], "role": r[2], "avatar_url": r[3], "email": r[4],
            "created_at": str(r[5])[:10] if r[5] else None,
            "last_login_at": str(r[6])[:19] if r[6] else None,
            "last_login_ip": r[7],
            "phone": r[10],   # 批30：本人资料不脱敏（对齐 email 先例）
            "deactivated": r[9] is not None,      # 响应映射（真实列 deleted_at——盲审B-P1-3）
            "market_op": market_op}



# ——— 批20 20C：邮箱修改（验证成功才改——无 email_verified 列、无 pending 态，方案 v2 单事务四防线） ———

import re as _re_email
_EMAIL_RE = _re_email.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
from .alerts import _PHONE_RE   # 批30：单源（A-P2-5——alerts.py 已有 ^1[3-9]\d{9}$，不造第二份）


@router.post("/api/user/email-change")
def email_change_api(req: EmailChangeReq, request: Request,
                     background_tasks: BackgroundTasks,
                     payload: dict = Depends(require_authenticated)):
    """发起改邮箱：密码验证+占用校验 → 发确认邮件到新邮箱（1h token）。库零触碰直到 confirm。"""
    if _rate_limited("emailchg", request.client.host if request.client else "?"):
        raise ApiError(429, "RATE_LIMITED", "请求过于频繁，请稍后再试")
    uid = int(payload["sub"])
    new_email = req.new_email.strip().lower()          # 规范化全链（盲审A-P2-5：唯一约束区分大小写）
    if not _EMAIL_RE.fullmatch(new_email):
        raise ApiError(400, "EMAIL_INVALID", "邮箱格式不正确")
    from ..auth import verify_password, create_token
    with get_conn() as conn:
        cur = conn.execute("SELECT email, password_hash FROM users WHERE id=%s "
                           "AND deleted_at IS NULL AND enabled=true", (uid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(403, "ACCOUNT_UNAVAILABLE", "账号不可用")
        if not row[1] or not verify_password(req.current_password, row[1]):
            raise ApiError(400, "OLD_PASSWORD_WRONG", "当前密码错误")
        if new_email == (row[0] or "").lower():
            raise ApiError(400, "SAME_AS_CURRENT", "新邮箱与当前邮箱相同")
        occ = conn.execute("SELECT 1 FROM users WHERE email=%s AND id<>%s AND deleted_at IS NULL",
                           (new_email, uid)).fetchone()
        if occ:
            raise ApiError(409, "EMAIL_TAKEN", "该邮箱已被使用")
    token = create_token(new_email, "email_change", user_id=uid, hours=1)
    background_tasks.add_task(send_email_change_email, new_email, token, _request_base(request), req.lang)
    return {"status": "sent"}


@router.post("/api/user/email-change/confirm")
def email_change_confirm_api(req: EmailConfirmReq):
    """确认改邮箱（免登录——token 即凭证）。单事务四防线：占用复核/账号状态守卫/token 原子标记/
    唯一约束竞态转 409。任何失败=库零触碰（验证不成功根本不改——用户裁定）。"""
    from ..auth import verify_token
    t = verify_token(req.token, "email_change")
    if not t or not t.get("user_id"):
        raise ApiError(400, "TOKEN_INVALID_OR_EXPIRED", "链接无效或已过期")
    new_email = t["email"]
    try:
        with get_conn() as conn:
            occ = conn.execute("SELECT 1 FROM users WHERE email=%s AND id<>%s AND deleted_at IS NULL",
                               (new_email, t["user_id"])).fetchone()
            if occ:
                raise ApiError(409, "EMAIL_TAKEN", "该邮箱已被使用")
            old = conn.execute("SELECT email FROM users WHERE id=%s", (t["user_id"],)).fetchone()
            cur = conn.execute(
                "UPDATE users SET email=%s WHERE id=%s AND deleted_at IS NULL AND enabled=true "
                "RETURNING username", (new_email, t["user_id"]))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                raise ApiError(410, "ACCOUNT_GONE", "账号已注销或停用，邮箱未修改")
            marked = conn.execute("UPDATE user_tokens SET used=true WHERE id=%s AND used=false "
                                  "RETURNING id", (t["id"],)).fetchone()
            if not marked:
                conn.rollback()   # 并发双 confirm：后到者见 used=true——幂等拒
                raise ApiError(400, "TOKEN_INVALID_OR_EXPIRED", "链接已使用")
            conn.commit()
        audit_log(row[0], "email_change", row[0], f"{(old[0] if old else '') or '-'}->{new_email}")   # 批27-14：target=用户名（原与 action 重复，按 target 聚合检索错位）
        return {"status": "changed", "email": new_email}
    except ApiError:
        raise
    except Exception as e:
        pgcode = getattr(getattr(e, "orig", None), "pgcode", "") or getattr(e, "pgcode", "")
        if pgcode == "23505":
            raise ApiError(409, "EMAIL_TAKEN", "该邮箱已被使用")
        raise


# ——— 批30：手机号修改（验证码验证成功才改——码绑死手机：存侧 {code,phone}，change 不收 body 手机号）———

def _mask_phone(p: str | None) -> str:
    return f"{p[:3]}****{p[-4:]}" if p and len(p) == 11 else (p or "")


@router.post("/api/user/phone-request")
def phone_request_api(req: PhoneCodeReq, payload: dict = Depends(require_authenticated)):
    """发起改手机：密码验证+格式校验 → 6 位码发目标手机（码与手机号同存 Valkey TTL 5min）。

    防线（批20 同构+盲审 A/B）：密码（被盗会话防）/频控 phonechg 3/h 按 uid（emailchg 用
    client.host 在反代后全站共桶=既有坑不复制）/60s 发码冷却/码值含手机号（A-P0-1：body
    提交他号+正确码≠通过——change 只认存侧号）。"""
    uid = int(payload["sub"])
    if _rate_limited("phonechg", str(uid)):
        raise ApiError(429, "PHONE_RATE_LIMITED", "1 小时内最多发送 3 次验证码")
    phone = (req.phone or "").strip()
    if not _PHONE_RE.fullmatch(phone):
        raise ApiError(400, "PHONE_INVALID", "手机号格式不正确（仅支持中国大陆）")
    from ..auth import verify_password
    with get_conn() as conn:
        cur = conn.execute("SELECT phone, password_hash FROM users WHERE id=%s "
                           "AND deleted_at IS NULL AND enabled=true", (uid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(403, "ACCOUNT_UNAVAILABLE", "账号不可用")
        if not row[1] or not verify_password(req.current_password, row[1]):
            raise ApiError(400, "OLD_PASSWORD_WRONG", "当前密码错误")
        if phone == (row[0] or ""):
            raise ApiError(400, "PHONE_SAME_AS_CURRENT", "新手机号与当前手机号相同")   # B-P2-2：独立码——复用邮箱码会弹邮箱文案
    from ..redis_pool import redis_client as _rc
    import secrets as _secrets
    import json as _json
    r = _rc()
    if not r.set(f"phone:chg:cd:{uid}", "1", nx=True, ex=60):
        raise ApiError(429, "PHONE_CODE_TOO_FREQUENT", "验证码 60 秒内只能获取一次")
    code = f"{_secrets.randbelow(1000000):06d}"
    r.set(f"phone:chg:{uid}", _json.dumps({"code": code, "phone": phone}), ex=300)
    from src.alert_notify.sms import send_sms_code
    ok, reason = send_sms_code(phone, code)
    if not ok:
        r.delete(f"phone:chg:cd:{uid}")   # 发码失败退还冷却（防占坑）
        raise ApiError(502, "SMS_SEND_FAILED", f"验证码发送失败（{reason}）")
    audit_log(payload["username"], "phone_code_sent", payload["username"], _mask_phone(phone))
    return {"status": "sent"}


@router.post("/api/user/phone-change")
def phone_change_api(req: PhoneChangeReq, payload: dict = Depends(require_authenticated)):
    """确认改手机（登录态）：码对 → UPDATE users.phone=存侧手机号。码错 5 次作废（A-P1-6）；
    比对用 compare_digest（A-P2-7 防时序逐位爆破）。"""
    uid = int(payload["sub"])
    from ..redis_pool import redis_client as _rc
    import secrets as _secrets
    import json as _json
    r = _rc()
    raw = r.get(f"phone:chg:{uid}")
    data = None
    try:
        data = _json.loads(raw) if raw else None
    except Exception:
        data = None
    code_in = (req.code or "").strip()
    if not data or not code_in or not _secrets.compare_digest(str(data.get("code", "")), code_in):
        fails = r.incr(f"phone:chg:fails:{uid}")
        r.expire(f"phone:chg:fails:{uid}", 300)
        if fails >= 5:
            r.delete(f"phone:chg:{uid}", f"phone:chg:fails:{uid}")   # 码作废
        raise ApiError(400, "PHONE_CODE_INVALID", "验证码无效或已过期")
    phone = str(data.get("phone", ""))
    if not _PHONE_RE.fullmatch(phone):   # 存侧防御（理论不可达）
        raise ApiError(400, "PHONE_CODE_INVALID", "验证码无效或已过期")
    with get_conn() as conn:
        cur = conn.execute("SELECT phone FROM users WHERE id=%s AND deleted_at IS NULL AND enabled=true",
                           (uid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(403, "ACCOUNT_UNAVAILABLE", "账号不可用")
        old = row[0] or ""
        conn.execute("UPDATE users SET phone=%s WHERE id=%s", (phone, uid))
        conn.commit()
    r.delete(f"phone:chg:{uid}", f"phone:chg:fails:{uid}")
    audit_log(payload["username"], "phone_change", payload["username"],
              old_value=_mask_phone(old) if old else "", new_value=_mask_phone(phone))
    return {"ok": True, "phone": _mask_phone(phone)}


@router.post("/api/user/profile")
def profile_update_api(body: dict = Body(...),
                       payload: dict = Depends(require_authenticated)):
    """更新昵称（批次C；仅昵称可自助改，角色/用户名只读）。"""
    nickname = str(body.get("nickname", "")).strip()[:20]
    if not nickname:
        raise ApiError(400, "NICKNAME_REQUIRED", "昵称不能为空")
    with get_conn() as conn:
        conn.execute("UPDATE users SET nickname=%s WHERE id=%s", (nickname, payload["sub"]))
        conn.commit()
    audit_log(payload["username"], "update_profile", f"nickname={nickname}")
    return {"ok": True}


def _save_avatar_base64(data: str, uid: int) -> str:
    """头像 base64 → 中心裁剪 256px JPEG 存 static/avatars/user_{uid}.jpg,返回 URL。

    批11 从 /api/user/avatar 端点下沉（注册开通四字段复用——彼时用户刚建,无登录态）。"""
    import base64 as _b64
    import io as _io
    import time as _time
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
        fname = f"user_{uid}.jpg"
        img.save(_AVATAR_DIR / fname, "JPEG", quality=85)
    except ApiError:
        raise
    except Exception:
        raise ApiError(400, "AVATAR_INVALID", "图片解析失败")
    return f"/api/static/avatars/{fname}?t={int(_time.time())}"


@router.post("/api/user/avatar")
def avatar_upload_api(body: dict = Body(...),
                      payload: dict = Depends(require_authenticated)):
    """设置头像（批次C+）：{icon:"icon_NN.png"} 选系统卡通图标（public/icons，36 个）
    或 {avatar_base64} 上传（裁剪 1:1 → Pillow 统一 256px JPEG 存 static/avatars，
    固定文件名 user_{id}.jpg 覆盖旧图无孤儿，URL 带 ?t= 防缓存）。"""
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
    url = _save_avatar_base64(str(body.get("avatar_base64", "")), payload["sub"])
    with get_conn() as conn:
        conn.execute("UPDATE users SET avatar_url=%s, avatar_updated_at=now() WHERE id=%s",
                     (url, payload["sub"]))
        conn.commit()
    audit_log(payload["username"], "avatar_upload")
    return {"avatar_url": url}


@router.post("/api/user/deactivate")
def deactivate_api(authorization: str = Header(...),
                   payload: dict = Depends(require_authenticated)):
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


@router.post("/api/invites/batch-delete")
def invites_batch_delete_api(body: dict = Body(...),
                             payload: dict = Depends(require_perm("user_mgmt"))):
    """批量删邀请记录（批11：清理用）。任何状态（pending/used/expired/revoked）皆可删；上限 100/次。"""
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise ApiError(400, "IDS_EMPTY", "ids 不能为空")
    if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ApiError(400, "IDS_INVALID", "ids 须为整型数组")   # 盲审 P2-1：text[]/float[] 到 PG 报 operator 错=500
    if len(ids) > 100:
        raise ApiError(400, "TOO_MANY", "单次最多删除 100 条")
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM user_tokens WHERE type='invite' AND id = ANY(%s)", (ids,))
        conn.commit()
        deleted = cur.rowcount
    audit_log(payload["username"], "invites_batch_delete", f"n={deleted}", f"ids={ids}")   # 盲审 P2-8：不可逆删除可追溯
    return {"deleted": deleted}


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
    """自助开通：凭 invite token 建用户（默认 Viewer）。批11 四字段：用户名/昵称/头像/密码。"""
    validate_password(req.password)  # 不达标直接抛 ApiError(含错误码)
    username = (req.username or "").strip()
    if not username:
        raise ApiError(400, "USERNAME_EMPTY", "用户名不能为空")   # 盲审 P2-3：strip 后空串后端裸奔
    if _username_reserved(username):
        raise ApiError(400, "USERNAME_RESERVED", "该用户名为系统保留名")
    user = register_user(req.token, username, req.password, nickname=(req.nickname or "").strip())
    if not user:
        raise ApiError(400, "TOKEN_OR_USERNAME_INVALID", "token 无效/已用/过期，或用户名已存在")
    if req.avatar:
        # 盲审 P1-1：此时账号已建+token 已烧——头像失败绝不能把开通流程带进死胡同（重试同 token 必 400）
        try:
            url = _save_avatar_base64(req.avatar, user["id"])
            with get_conn() as conn:
                conn.execute("UPDATE users SET avatar_url=%s, avatar_updated_at=now() WHERE id=%s",
                             (url, user["id"]))
                conn.commit()
        except Exception as e:
            import logging as _lg
            _lg.getLogger("web_api").warning("注册头像保存失败(降级跳过): %s", e)   # 选填项,降级不阻断
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
def change_password_api(req: ChangePwdReq, payload: dict = Depends(require_authenticated)):
    """改密码：需旧密码验证。"""
    validate_password(req.new_password)  # 不达标直接抛 ApiError(含错误码)
    ok = change_password(int(payload["sub"]), req.old_password, req.new_password)
    if not ok:
        raise ApiError(400, "OLD_PASSWORD_WRONG", "旧密码错误")
    audit_log(payload["username"], "change_password")
    return {"status": "changed"}


# ——— 用户组管理（批11B：四角色硬编码 → DB 用户组实体；方案双盲审 A/B 修订全吸收） ———

import re as _re_group
_GROUP_NAME_RE = _re_group.compile(r"^[a-z][a-z0-9_]{1,29}$")   # 总长 2..30（盲审 B P2-4）


@router.get("/api/user-groups")
def list_groups(payload: dict = Depends(require_perm("user_mgmt"))):
    """组列表（user_count 只数活跃用户——排除软删，盲审 B P1-2）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, g.description, g.builtin, "
            "COALESCE(u.cnt, 0) FROM user_group g LEFT JOIN "
            "(SELECT role, count(*) cnt FROM users WHERE deleted_at IS NULL GROUP BY role) u "
            "ON u.role = g.name ORDER BY g.builtin DESC, g.id").fetchall()
    return [{"id": r[0], "name": r[1], "description": r[2] or "",
             "builtin": r[3], "user_count": r[4]} for r in rows]


@router.post("/api/user-groups")
def create_group(body: dict = Body(...), payload: dict = Depends(require_perm("user_mgmt"))):
    """建组（新组零权限起步——permission 无行不落字典兜底）。body.builtin 服务端忽略（盲审 A P2）。"""
    name = str(body.get("name", "")).strip()
    desc = str(body.get("description", "") or "").strip()[:200]
    if not _GROUP_NAME_RE.fullmatch(name):
        raise ApiError(400, "GROUP_NAME_INVALID", "组名 2-30 位、小写字母开头、可含数字/下划线")
    try:
        with get_conn() as conn:
            gid = conn.execute(
                "INSERT INTO user_group (name, description) VALUES (%s,%s) RETURNING id",
                (name, desc)).fetchone()[0]
            conn.commit()
    except Exception as e:
        if getattr(e, "pgcode", None) == "23505" or "duplicate key" in str(e).lower():
            raise ApiError(409, "GROUP_EXISTS", f"组名已存在: {name}")
        raise
    audit_log(payload["username"], "group_create", name)
    return {"id": gid, "name": name}


@router.post("/api/user-groups/{gid}")
def update_group(gid: int, body: dict = Body(...), payload: dict = Depends(require_perm("user_mgmt"))):
    """改组。builtin：锁 name 只可改 description；自定义组：rename 三表级联（单事务+尾断言防并发孤儿行，
    盲审 B P2-1）+软删用户 role 一并归位；rename 后 invalidate_perm_cache（盲审 A/B 同判 P1）。"""
    from ..auth import invalidate_perm_cache
    desc = str(body.get("description", "") or "").strip()[:200]
    new_name = str(body.get("name", "") or "").strip()
    renamed = False
    with get_conn() as conn:
        row = conn.execute("SELECT name, builtin FROM user_group WHERE id=%s", (gid,)).fetchone()
        if not row:
            raise ApiError(404, "GROUP_NOT_FOUND", "用户组不存在")
        old_name, builtin = row
        if builtin and new_name and new_name != old_name:
            raise ApiError(400, "GROUP_BUILTIN", "内置组不可改名")
        if not builtin and new_name and new_name != old_name:
            if not _GROUP_NAME_RE.fullmatch(new_name):
                raise ApiError(400, "GROUP_NAME_INVALID", "组名 2-30 位、小写字母开头、可含数字/下划线")
            if conn.execute("SELECT 1 FROM user_group WHERE name=%s AND id<>%s",
                            (new_name, gid)).fetchone():
                raise ApiError(409, "GROUP_EXISTS", f"组名已存在: {new_name}")   # 代码盲审 B P1-1：撞名（含内置名）原裸抛 UniqueViolation=500
            conn.execute("UPDATE user_group SET name=%s, description=%s WHERE id=%s", (new_name, desc, gid))
            perm_n = conn.execute(
                "UPDATE permission SET subject_id=%s WHERE subject_type='role' AND subject_id=%s",
                (new_name, old_name)).rowcount
            user_n = conn.execute(
                "UPDATE users SET role=%s WHERE role=%s AND deleted_at IS NULL",
                (new_name, old_name)).rowcount
            conn.execute("UPDATE users SET role='viewer' WHERE role=%s AND deleted_at IS NOT NULL",
                         (old_name,))   # 代码盲审 A P2-3：软删用户一并归位（与 delete 对称，防幽灵组名残留展示）
            # 级联尾断言：rename×权限写并发（READ COMMITTED 语句快照看不见对方新 INSERT 的旧行）
            if conn.execute("SELECT 1 FROM permission WHERE subject_type='role' AND subject_id=%s LIMIT 1",
                            (old_name,)).fetchone():
                conn.rollback()
                raise ApiError(409, "GROUP_RENAME_RACE", "权限并发写入，请重试")
            conn.commit()
            renamed = True
            rename_log = (payload["username"], f"{old_name}->{new_name}",
                          f"permission行={perm_n} 用户行={user_n}")
        else:
            conn.execute("UPDATE user_group SET description=%s WHERE id=%s", (desc, gid))
            conn.commit()
            rename_log = (payload["username"], "group_update", old_name, f"desc({len(desc)}字)")
    if renamed:
        invalidate_perm_cache()
        audit_log(*rename_log)   # 代码盲审 A P3：commit 后在事务外记——audit 抖动不再把已提交操作报 500
    else:
        audit_log(*rename_log)
    return {"ok": True}


@router.delete("/api/user-groups/{gid}")
def delete_group(gid: int, payload: dict = Depends(require_perm("user_mgmt"))):
    """删组（builtin 拒；活跃用户>0 拒；级联清 permission 行+软删用户 role 归 viewer；
    invalidate_perm_cache 防 60s 内重建同名组继承已删权限——盲审 A/B 同判 P1）。"""
    from ..auth import invalidate_perm_cache
    with get_conn() as conn:
        row = conn.execute("SELECT name, builtin FROM user_group WHERE id=%s", (gid,)).fetchone()
        if not row:
            raise ApiError(404, "GROUP_NOT_FOUND", "用户组不存在")
        name, builtin = row
        if builtin:
            raise ApiError(400, "GROUP_BUILTIN", "内置组不可删除")
        cnt = conn.execute("SELECT count(*) FROM users WHERE role=%s AND deleted_at IS NULL",
                           (name,)).fetchone()[0]
        if cnt:
            raise ApiError(409, "GROUP_IN_USE", f"组下仍有 {cnt} 个活跃用户，请先移出再删除")
        perm_n = conn.execute("DELETE FROM permission WHERE subject_type='role' AND subject_id=%s",
                              (name,)).rowcount
        soft_n = conn.execute("UPDATE users SET role='viewer' WHERE role=%s AND deleted_at IS NOT NULL",
                              (name,)).rowcount
        conn.execute("DELETE FROM user_group WHERE id=%s", (gid,))
        conn.commit()
    invalidate_perm_cache()
    audit_log(payload["username"], "group_delete", name, f"permission行={perm_n} 软删用户归位={soft_n}")
    return {"ok": True}


# ——— 用户管理（Admin） ———

@router.post("/api/user")
def create_user_api(req: UserCreate, payload: dict = Depends(require_perm("user_mgmt"))):
    validate_password(req.password)   # P0-复审残留：admin 建用户原无校验（>72 字节 500）
    if _username_reserved(req.username):   # 批26-8：admin 面同闸（原无校验——与注册路径一致性）
        raise ApiError(400, "USERNAME_RESERVED", "该用户名为系统保留名")
    # 批11B：四元组白名单 → user_group 存在性校验（角色值域动态化）
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM user_group WHERE name=%s", (req.role,)).fetchone():
            raise ApiError(400, "ROLE_INVALID", f"用户组不存在: {req.role}")
    try:
        uid = create_user(req.username, req.password, req.role)
        audit_log(payload["username"], "create_user", req.username, f"role={req.role}")
        return {"id": uid, "username": req.username, "role": req.role}
    except ValueError as e:
        raise ApiError(409, "USERNAME_EXISTS", str(e))


@router.get("/api/user")
def list_users(payload: dict = Depends(require_perm("user_mgmt"))):
    # 批11：email_verified 列已删（迁移 0070）；email 保留返回（邀请/通知流程用）
    with get_conn() as conn:
        cur = conn.execute("SELECT id, username, nickname, role, enabled, email, created_at, "
                           "last_login_at, deleted_at FROM users ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "username": r[1], "nickname": r[2], "role": r[3],
             "enabled": r[4] and not r[8], "deactivated": bool(r[8]),
             "email": r[5], "created_at": str(r[6])[:19],
             "last_login_at": str(r[7])[:19] if r[7] else None} for r in rows]


@router.post("/api/user/{uid}")
def update_user(uid: int, role: str = None, enabled: bool = None,
                payload: dict = Depends(require_perm("user_mgmt"))):
    """改用户角色/禁用。email 不在此面（批24 迭代十一用户裁定：用户在个人中心自助改——批20 邮件验证链）。
    保护：不能动自己（末位 admin 由 user_mgmt=admin-only + 不动自己 隐式保证）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT username FROM users WHERE id=%s", (uid,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
    guard_user_mutation(row[0], payload["username"])
    with get_conn() as conn:
        if role is not None:
            # 批11B（顺修盲审 P2-11 预先存在项）：update_user 原无角色校验——与 create 同为组存在性校验
            if not conn.execute("SELECT 1 FROM user_group WHERE name=%s", (role,)).fetchone():
                raise ApiError(400, "ROLE_INVALID", f"用户组不存在: {role}")
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


@router.get("/api/log")
def get_logs(task_id: str | None = None, before: str | None = None, limit: int = 100,
             level: list[str] = Query(default=[]), module: list[str] = Query(default=[]),
             payload: dict = Depends(require_perm("user_mgmt"))):
    """运行日志（批25：无参=system_log 全局面——三通道发送事件 module=email|im|sms 天然在内；
    task_id 参数=live-task 自愈时间线，保留读 task_logs（唯一消费 LiveTask，对分页参数忽略）。
    权限 read→user_mgmt 收紧（用户裁定：运行日志=管理员面，含收件人明文）。
    批32：游标分页（每页 100——用户裁定；ORDER BY ts DESC, id DESC 双键=log_sink 同事务批量
    同 ts 单键必丢/重行，盲审 A-P1-1/B-P0）+level/module 筛选后端化（FastAPI 重复参数零逗号
    歧义）+首屏（无 before）附带 modules 全量下拉源（翻页不重复——A-P2-3）。游标独立全精度
    序列化 epochµs|id（展示字段仍 str[:19]——截秒游标必漏行，A-P1-2）。"""
    limit = max(1, min(limit, 500))
    if task_id:
        rows = []
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT level, message, step_name, created_at FROM task_logs "
                    "WHERE task_id = %s ORDER BY created_at DESC LIMIT 100", (task_id,))   # 精确匹配（LIKE %live:1% 撞 live:12）；批32：对分页/筛选参数忽略（LiveTask 时间线语义）
                rows = cur.fetchall()
        except Exception:
            logger.warning("get_logs: task_logs 表不存在（需运行 alembic upgrade head）")
        return {"logs": [{"level": r[0], "msg": r[1], "module": r[2] or "",
                          "ts": str(r[3])[:19] if r[3] else ""} for r in rows]}
    where, params = "", []
    if before:
        try:
            _us, _rid = before.split("|")
            from datetime import datetime as _dt, timezone as _tz
            _ts = _dt.fromtimestamp(int(_us) / 1_000_000, tz=_tz.utc)
            where += " WHERE (ts, id) < (%s, %s)"
            params += [_ts, int(_rid)]
        except Exception:
            raise ApiError(400, "CURSOR_INVALID", "游标无效或已过期，请刷新列表")
    _lv = [lv for lv in level if lv]   # B-P2-2：全空串维度跳过（ANY('{}') 恒假会拉空结果）
    if _lv:
        where += (" AND" if where else " WHERE") + " level = ANY(%s)"
        params.append(_lv)
    _md = [m for m in module if m]
    if _md:
        where += (" AND" if where else " WHERE") + " module = ANY(%s)"
        params.append(_md)
    try:
        with get_conn() as conn:
            cur = conn.execute(
                f"SELECT id, level, module, message, ts FROM system_log{where} "
                "ORDER BY ts DESC, id DESC LIMIT %s", (*params, limit))
            rows = cur.fetchall()
            mods = []
            if not before:   # 首屏才带下拉源（翻页值域不变——A-P2-3）
                mcur = conn.execute("SELECT DISTINCT module FROM system_log WHERE module <> '' ORDER BY 1")
                mods = [m[0] for m in mcur.fetchall()]
    except Exception:
        logger.warning("get_logs: system_log 表不存在（需运行 alembic upgrade head）")
        rows, mods = [], []
    logs = [{"level": r[1], "msg": r[3], "module": r[2] or "",
             "ts": str(r[4])[:19] if r[4] else ""} for r in rows]
    next_cursor = None
    if len(rows) == limit and rows:   # 恰好一页=可能还有；不足=终页
        last = rows[-1]
        next_cursor = f"{int(last[4].timestamp() * 1_000_000)}|{last[0]}"
    return {"logs": logs, "next": next_cursor, "modules": mods}