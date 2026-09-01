"""Web 后端 · 认证(JWT) + RBAC + 审计日志。

单系统多用户，四角色共享数据，仅权限分层。非多租户。
Trader（交易：启停策略/熔断/下单）与 Analyst（研究：策略/回测/数据同步）隔离防误操作。
"""

from __future__ import annotations
from src.data_platform.db import get_conn
import os
import re
import time
import bcrypt
import secrets
from typing import Literal
from datetime import datetime, timedelta, timezone
import jwt
import psycopg
import logging
from fastapi import HTTPException, Header
from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger("quant")

Role = Literal["viewer", "analyst", "trader", "admin"]
# JWT_SECRET 优先级：SECRET_KEY 派生 > JWT_SECRET 环境变量 > 默认值
_secret_key = os.environ.get("SECRET_KEY", "")
if _secret_key:
    from src.quant_common.crypto import _derive_key
    JWT_SECRET = _derive_key(_secret_key, b"jwt")
else:
    JWT_SECRET = os.environ.get("JWT_SECRET", "quant-dev-secret-change-me")
if _secret_key:
    pass  # 根密钥派生，无告警
elif JWT_SECRET == "quant-dev-secret-change-me":
    # SD1（F-32）：默认密钥+实盘开关=可伪造任意角色 token（含解密凭证链），组合必须拒绝启动
    try:
        from src.data_platform.settings import is_live_trading_enabled
        _live = is_live_trading_enabled()
    except Exception:
        _live = False
    if _live:
        raise RuntimeError("生产配置错误：ENABLE_LIVE_TRADING=true 时禁止默认 JWT_SECRET，请在 .env 设置独立密钥")
    _logger.warning("JWT_SECRET 使用默认值，生产环境请通过环境变量设置独立密钥")
JWT_ALGO = "HS256"
JWT_TTL_HOURS = 24

# ——— 权限矩阵（4 级：Admin/Trader/Analyst/Viewer）——

# P3-7（web-design 10 §6 阶段 A）：字典保留为 fallback（表空/DB 故障时行为兜底，改权限不发版）；
# 运行时真源=permission 表（60s 缓存热加载），_load_permissions() 合并 deny>allow>默认拒绝。
PERMISSIONS = {
    "viewer":  {"read"},
    "analyst": {"read", "strategy_control", "data_sync", "system_config"},   # 研究：策略/回测/数据同步/系统配置
    "trader":  {"read", "strategy_control", "halt", "trade", "live_trading_control"},  # 交易：策略启停/熔断/下单/实盘开关
    "admin":   {"read", "strategy_control", "data_sync", "halt", "resume", "trade", "live_trading_control",
                 "risk_rules", "account_keys", "user_mgmt", "system_config", "llm_config", "im_bots_config"},
}

_PERM_CACHE: dict = {"at": 0.0, "roles": None, "users": {}}
_PERM_TTL = 60.0

# W4（盲审 B-P0 新建——原"系统策略键已锁定"是幻觉：require_perm 全表驱动,任何键今天都可被
# 角色重写关掉）：锁键=提权链/自损链高危键——角色重写与 user override 双路径同锁;
# admin 角色重写另有地板键（self-lockout 防线）
LOCKED_PERM_KEYS = {"user_mgmt", "resume", "account_keys"}
ADMIN_ROLE_FLOOR = LOCKED_PERM_KEYS | {"system_config"}


def load_role_permissions() -> dict:
    """角色→权限集（api 维）。表读失败/空 → fallback 字典（行为零变化）。"""
    import time as _t
    now = _t.time()
    if _PERM_CACHE["roles"] is not None and now - _PERM_CACHE["at"] < _PERM_TTL:
        return _PERM_CACHE["roles"]
    try:
        from src.data_platform.db import get_conn as _gc
        with _gc() as conn:
            cur = conn.execute(
                "SELECT subject_id, resource, effect FROM permission "
                "WHERE subject_type='role' AND dimension='api'")
            roles: dict = {}
            for sid, res, eff in cur.fetchall():
                # W4 修沉疴：原 `grants, denies = setdefault(...)` 解包的是 dict 的键
                # （'allow'/'deny' 字符串）非值——表有行即 AttributeError 静默回退字典，
                # 管理页保存过的权限从未生效（产线表恒空所以从未暴露;W4 基线测试首触发）
                gd = roles.setdefault(sid, {"allow": set(), "deny": set()})
                (gd["deny"] if eff == "deny" else gd["allow"]).add(res)
        merged = {}
        for role, base in PERMISSIONS.items():
            tab = roles.get(role, {})
            # 终审 P1-4 修正：表有该 role 的 allow 行 → 全量以表为准（撤权/全量重写生效）；
            # 表无行（未管理过的角色）→ 字典兜底。deny 行始终从结果里减（10 §1 deny 优先）。
            # W4 修沉疴②：无表行的角色 tab={} → tab["allow"] KeyError 同被空表掩盖
            tab_allow = tab.get("allow") or set()
            allow = (tab_allow if tab_allow else base) - tab.get("deny", set())
            merged[role] = allow
        for role, gd in roles.items():
            if role not in PERMISSIONS:
                merged[role] = gd["allow"] - gd["deny"]
        _PERM_CACHE.update(at=now, roles=merged)
        return merged
    except Exception as e:
        _logger.warning("permission 表读取失败（回退字典）: %s", e)
        return PERMISSIONS


def invalidate_perm_cache() -> None:
    """权限变更后即刻生效（10 §3：指纹重编译）。全局清（W4 保持现语义——单 worker
    写后即生效,勿改按键清留 role 脏键,盲审 B-P1）。"""
    _PERM_CACHE.update(at=0.0, roles=None, users={})


def _load_user_api_overrides(username: str) -> tuple[set, set]:
    """user 维 api override →（allows, denies）。读失败 raise 由调用方决定 fail-open。"""
    from src.data_platform.db import get_conn as _gc
    with _gc() as conn:
        cur = conn.execute(
            "SELECT resource, effect FROM permission "
            "WHERE subject_type='user' AND subject_id=%s AND dimension='api'", (username,))
        allows, denies = set(), set()
        for res, eff in cur.fetchall():
            (denies if eff == "deny" else allows).add(res)
    return allows, denies


def data_sensitivity(username: str, role: str) -> str:
    """W5：data 维敏感级（detail|aggregated|count，缺省 detail=现行为零变化）。

    解析 resource='sensitivity:<v>' 行（W4 Permissions.vue 编码）。user 行覆盖
    role 行（单值字段的 deny 语义=用户级值生效）；无任何配置=detail。读失败=detail。
    """
    try:
        from src.data_platform.db import get_conn as _gc
        with _gc() as conn:
            rows = conn.execute(
                "SELECT subject_type, subject_id, resource FROM permission "
                "WHERE dimension='data' AND resource LIKE 'sensitivity:%'").fetchall()
        role_v = user_v = None
        for st, sid, res in rows:
            v = res.split(":", 1)[1]
            if st == "role" and sid == role:
                role_v = v
            elif st == "user" and sid == username:
                user_v = v
        return user_v or role_v or "detail"
    except Exception:
        return "detail"


def load_nav_map(username: str, role: str) -> dict:
    """W5：nav 维三态映射（resource=菜单id → hidden|readonly|readwrite）。

    user 行覆盖 role 行（与 data 维同规则）；无配置={}（=readwrite 缺省，前端现行为）。
    """
    try:
        from src.data_platform.db import get_conn as _gc
        with _gc() as conn:
            rows = conn.execute(
                "SELECT subject_type, subject_id, resource, effect FROM permission "
                "WHERE dimension='nav'").fetchall()
        out: dict = {}
        for st, sid, res, eff in rows:
            if st == "role" and sid == role:
                out[res] = eff
        for st, sid, res, eff in rows:
            if st == "user" and sid == username:
                out[res] = eff
        return out
    except Exception:
        return {}


def load_effective_permissions(username: str, role: str) -> tuple[set, dict]:
    """W4 C 阶段：用户有效权限 = user deny > user allow > role allow（10 §3 合并序）。

    返回 (perms, sources)：sources 供玻璃盒标注来源（api 维）——
    {"<perm>": "user-override" | "role-base"} + {"__denied__": [被 user deny 的 role 键]}。
    user 维读失败 fail-open=按角色（user 维无字典可回,盲审 A-P1）。
    """
    roles = load_role_permissions()
    base = set(roles.get(role, set()))
    if not username:
        return base, {p: "role-base" for p in base}
    key = (username, role)
    cached = _PERM_CACHE["users"].get(key)
    if cached is None:
        try:
            cached = _load_user_api_overrides(username)
            _PERM_CACHE["users"][key] = cached
        except Exception:
            # 盲审 A-P1b：失败结果**不缓存**（users 槽无 TTL,缓存=一次 DB 抖动把该用户
            # fail-open 冻结到下次 invalidate）——本次按角色返回,下次重试
            cached = (set(), set())
    allows, denies = cached
    denied = sorted(base & denies)
    perms = (base | allows) - denies
    sources = {p: ("user-override" if (p in allows and p not in base) else "role-base")
               for p in perms}
    sources["__denied__"] = denied
    return perms, sources


def require_role(*allowed: Role):
    """FastAPI 依赖：检查 JWT 角色是否在允许列表中。"""
    def checker(authorization: str = Header(...)):
        token = re.sub(r'^Bearer\s+', '', authorization, flags=re.IGNORECASE)
        payload = verify_jwt(token)
        # W4（盲审 B-P1）：require_role 同步改 DB role——降级后存量 token 不再 24h 越权
        role = payload.get("db_role") or payload.get("role", "viewer")
        if role not in allowed:
            raise HTTPException(403, f"角色 {role} 无权限，需 {allowed}")
        return payload
    return checker


def require_perm(perm: str):
    """FastAPI 依赖：检查 JWT 角色是否有指定权限。"""
    def checker(authorization: str = Header(...)):
        token = re.sub(r'^Bearer\s+', '', authorization, flags=re.IGNORECASE)
        payload = verify_jwt(token)
        # W4：DB role 优先（verify_jwt 已查 users 行,零成本——JWT role 仅旧 token 兜底）+
        # effective 解析（user override 并入）
        role = payload.get("db_role") or payload.get("role", "viewer")
        perms, _ = load_effective_permissions(payload.get("username", ""), role)
        if perm not in perms:
            raise HTTPException(403, f"角色 {role} 无 {perm} 权限")
        return payload
    return checker


# ——— JWT ———

def create_jwt(user_id: str, username: str, role: Role) -> str:
    import uuid
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=JWT_TTL_HOURS),
        "iat": datetime.utcnow(),
        "jti": uuid.uuid4().hex,  # 登出黑名单用（logout 置 Valkey jwt:bl:{jti}）
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def verify_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token 过期")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "token 无效")
    # 登出黑名单（A4）：旧 token 无 jti 跳过（24h 过渡期后全部带 jti）
    jti = payload.get("jti")
    if jti:
        import redis as _redis
        import os as _os
        r = _redis.Redis.from_url(_os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
        if r.exists(f"jwt:bl:{jti}"):
            raise HTTPException(401, "token 已登出")
    # SD1（F-45）：账号状态即时校验——禁用/注销后存量 token 立即失效（原来最长 24h 仍有效）
    username = payload.get("username")
    if username:
        try:
            with get_conn() as conn:
                cur = conn.execute("SELECT enabled, deleted_at, role FROM users WHERE username=%s", (username,))
                row = cur.fetchone()
            if not row or not row[0] or row[1]:
                raise HTTPException(401, "账号已禁用或注销")
            if row[2]:
                payload["db_role"] = row[2]   # W4：require_perm 改用 DB role——降级后存量 token 不再 24h 越权
        except HTTPException:
            raise
        except Exception as e:
            # users 表不可读：认证不可用即拒绝（fail-closed，SD1）
            raise HTTPException(401, f"账号状态校验失败: {e}")
    return payload


def revoke_jwt(token: str) -> bool:
    """把 token 的 jti 加入黑名单（TTL=剩余寿命）。登出即失效。"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.InvalidTokenError:
        return False
    jti = payload.get("jti")
    if not jti:
        return False  # 旧 token 无 jti（自然过期兜底）
    import redis as _redis
    import os as _os
    from datetime import datetime as _dt
    r = _redis.Redis.from_url(_os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    remaining = payload["exp"] - int(_dt.utcnow().timestamp())
    if remaining > 0:
        r.setex(f"jwt:bl:{jti}", remaining, "1")
    return True


# ——— 用户管理（PG） ———

def init_users_table():
    """初始化用户表 + 审计表（表已在 migration 0001 创建，保留接口兼容，不再 DDL）。"""
    return


def hash_password(password: str) -> str:
    """bcrypt 哈希（生产级，带 salt）。"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def validate_password(password: str) -> None:
    """密码复杂度校验：≥8 位，需含字母和数字。不达标抛 ApiError（细分错误码，前端 err.<CODE> 本地化）。

    上限 72 字节：bcrypt 5.x 对 >72 字节抛 ValueError→500（2026-08-20 双盲审计实测复现，P0-9）。
    """
    from .errors import ApiError
    if not password or len(password) < 8:
        raise ApiError(400, "PASSWORD_TOO_SHORT", "密码至少 8 位")
    if len(password.encode()) > 72:
        raise ApiError(400, "PASSWORD_TOO_LONG", "密码至多 72 字节（bcrypt 限制）")
    if not re.search(r"[A-Za-z]", password):
        raise ApiError(400, "PASSWORD_NO_LETTER", "密码需包含字母")
    if not re.search(r"[0-9]", password):
        raise ApiError(400, "PASSWORD_NO_DIGIT", "密码需包含数字")


def guard_user_mutation(target_username: str, current_username: str) -> None:
    """账户变更保护（DELETE / PUT user 共用，单一不变量）：
    不能动自己 —— 管理页不得删/改角色/禁用"自己"那行（防自我锁定）。
    注：「末位 admin 保护」已移除（2026-08-15）：user_mgmt 仅 admin 持有 + 不能动自己
    ⇒ 最后一个 admin 永远不可能被他人变更（他人必是另一个 admin ⇒ 目标不是末位），原规则不可达。
    """  # noqa: D418
    from .errors import ApiError
    if target_username == current_username:
        raise ApiError(400, "SELF_MUTATION_FORBIDDEN", "不能修改或删除当前登录的账户")


def verify_password(password: str, stored: str) -> bool:
    """bcrypt 验证（兼容旧 sha256：非 bcrypt 哈希返回 False 触发重置）。"""
    if not stored:
        return False
    try:
        return bcrypt.checkpw(password.encode(), stored.encode())
    except (ValueError, TypeError):
        return False  # 旧 sha256 哈希无法验证，触发 ensure_default_admin 重置


def create_user(username: str, password: str, role: Role = "viewer") -> int:
    """创建用户（Admin）。"""
    init_users_table()
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s,%s,%s) RETURNING id",
                (username, hash_password(password), role),
            )
            conn.commit()
            return cur.fetchone()[0]
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            raise ValueError(f"用户 {username} 已存在")


def authenticate(account: str, password: str) -> dict | None:
    """验证用户（支持 用户名 或 邮箱 登录：含 @ 按 email 查），返回 {id, username, role} 或 None。
    密码正确但账户被禁 → 抛 ApiError(ACCOUNT_DISABLED)（登录端点区分提示，不再误报密码错误）。
    已注销（deleted_at）视同不存在。"""
    init_users_table()
    field = "email" if "@" in account else "username"
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT id, username, password_hash, role, enabled FROM users "
            f"WHERE {field}=%s AND deleted_at IS NULL",
            (account,),
        )
        row = cur.fetchone()
    if not row:
        return None
    if not verify_password(password, row[2]):
        return None
    if not row[4]:
        from .errors import ApiError
        raise ApiError(403, "ACCOUNT_DISABLED", "账号已被禁用，请联系管理员")
    return {"id": row[0], "username": row[1], "role": row[3]}


def soft_delete_user(user_id: int) -> None:
    """软删除/注销（批次D，admin 删除与自助注销共用）：
    deleted_at 置时间 + email/昵称脱敏置空 + username 加后缀释放占用 + 头像文件清理由调用方做。"""
    import secrets as _secrets
    with get_conn() as conn:
        cur = conn.execute("SELECT username FROM users WHERE id=%s AND deleted_at IS NULL", (user_id,))
        row = cur.fetchone()
        if not row:
            return
        new_name = f"{row[0]}_deleted_{_secrets.token_hex(3)}"
        conn.execute(
            "UPDATE users SET deleted_at=now(), email=NULL, nickname=NULL, "
            "avatar_url=NULL, username=%s WHERE id=%s", (new_name, user_id))
        conn.commit()


def guard_self_deactivate(user_id: int) -> None:
    """自助注销保护（批次D）：唯一启用的 admin 不可注销自己。
    注：管理页删除路径无此约束（guard_user_mutation 注释——不可达）；自助注销是用户直接对自己
    的终局操作，末位 admin 场景在此路径真实可达，须设防。"""
    from .errors import ApiError
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT role FROM users WHERE id=%s AND deleted_at IS NULL AND enabled=true", (user_id,))
        row = cur.fetchone()
        if row and row[0] == "admin":
            cur2 = conn.execute(
                "SELECT count(*) FROM users WHERE role='admin' AND enabled=true AND deleted_at IS NULL")
            if cur2.fetchone()[0] <= 1:
                raise ApiError(400, "LAST_ADMIN_PROTECTED", "最后一个管理员不可注销自己")


# 2026-08-19 模块归位：audit_log 下沉 data_platform/audit（feishu_bot 曾因此反向 import 顶层）；
# 此 re-export 保本模块旧调用方零改动
from src.data_platform.audit import audit_log  # noqa: F401


def _default_admin_password() -> str:
    """SD1（F-46）：实盘模式下初始/重置密码随机生成（防"恢复备份→admin/admin123 自动复活"）。"""
    try:
        from src.data_platform.settings import is_live_trading_enabled
        if is_live_trading_enabled():
            import secrets as _sec
            pwd = _sec.token_urlsafe(12)
            _logger.critical("生产模式：admin 初始密码已随机生成（仅本次打印，请立即登录修改）: %s", pwd)
            return pwd
    except Exception:
        pass
    return "admin123"


def ensure_default_admin():
    """确保有默认 admin 账号（首次启动）+ 旧 sha256 密码重置为 bcrypt。"""
    init_users_table()
    with get_conn() as conn:
        cur = conn.execute("SELECT password_hash FROM users WHERE username='admin'")
        row = cur.fetchone()
        if not row:
            create_user("admin", _default_admin_password(), "admin")
            return True
        # 旧 sha256 密码（非 $2b$ 开头）重置为 bcrypt
        if not row[0].startswith("$2b$"):
            conn.execute("UPDATE users SET password_hash=%s WHERE username='admin'",
                         (hash_password(_default_admin_password()),))
            conn.commit()
            return True
        return False


# ——— 邀请制用户管理（邀请/开通/改密码/找回）———

def create_token(email: str, token_type: str, user_id: int | None = None, hours: int = 72) -> str:
    """生成 token 存 user_tokens（默认 3 天，password_reset 1 小时）。"""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=hours)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_tokens (user_id, email, token, type, expires_at) VALUES (%s,%s,%s,%s,%s)",
            (user_id, email, token, token_type, expires))
        conn.commit()
    return token


def verify_token(token: str, token_type: str) -> dict | None:
    """校验 token（未用过 + 未撤销 + 未过期）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, user_id, email, expires_at, used, revoked FROM user_tokens WHERE token=%s AND type=%s",
            (token, token_type))
        row = cur.fetchone()
    if not row or row[4] or row[5]:  # used / revoked
        return None
    if row[3] < datetime.now(timezone.utc):  # expired（DB 列 timestamptz aware，用 aware UTC 比较）
        return None
    return {"id": row[0], "user_id": row[1], "email": row[2]}


def _mark_token_used(token_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE user_tokens SET used=true WHERE id=%s", (token_id,))
        conn.commit()


def invite_user(email: str) -> str | None:
    """admin 邀请：检查 email 未注册，生成 invite token（3 天）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            return None  # 已注册
    return create_token(email, "invite", hours=72)


def register_user(token: str, username: str, password: str) -> dict | None:
    """自助开通：凭 invite token 建用户（默认 Viewer）。"""
    t = verify_token(token, "invite")
    if not t:
        return None
    try:
        uid = create_user(username, password, "viewer")
    except ValueError:
        return None  # 用户名已存在
    with get_conn() as conn:
        conn.execute("UPDATE users SET email=%s, email_verified=true WHERE id=%s", (t["email"], uid))
        conn.commit()
    _mark_token_used(t["id"])
    return {"id": uid, "username": username, "email": t["email"]}


def forgot_password(email: str) -> str | None:
    """找回密码：检查 email 存在，生成 reset token（1 小时）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT id FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
    if not row:
        return None
    return create_token(email, "password_reset", user_id=row[0], hours=1)


def reset_password(token: str, new_password: str) -> bool:
    """凭 reset token 重置密码。"""
    t = verify_token(token, "password_reset")
    if not t:
        return False
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash=%s WHERE email=%s",
                     (hash_password(new_password), t["email"]))
        conn.commit()
    _mark_token_used(t["id"])
    return True


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """改密码：需旧密码验证。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT password_hash FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        if not row or row[0] is None:
            raise HTTPException(400, "用户密码未设置")
        if not verify_password(old_password, row[0]):
            return False
        conn.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                     (hash_password(new_password), user_id))
        conn.commit()
    return True