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
# 批11C：权限解析下沉 data_platform/perms.py（IM 身份链同源）；此处 re-export 兼容全部既有引用
from src.data_platform.perms import (   # noqa: F401
    PERMISSIONS, LOCKED_PERM_KEYS, ADMIN_ROLE_FLOOR,
    load_role_permissions, invalidate_perm_cache, load_effective_permissions,
    data_sensitivity, load_nav_map,
)


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


def require_authenticated(authorization: str = Header(...)):
    """FastAPI 依赖：已认证+账号有效即可，**不查权限**（批11B 双盲审 P0-1 修）。

    身份与权限分层：身份类端点（me/logout/profile/avatar/deactivate/change-password）
    对自定义组（零权限起步）用户必须可用——原 require_role(四元组) 会让这类用户
    登录即 403 半砖死（连登出/改密都不可）。verify_jwt 已 fail-closed 校验账号
    存在/启用/未删（SD1）+登出黑名单（A4），此处仅作依赖别名；各端点自身的安全
    （旧密码验证/guard_self_deactivate/自助注销脱敏）保留不变。功能端点走 require_perm。
    """
    token = re.sub(r'^Bearer\s+', '', authorization, flags=re.IGNORECASE)
    return verify_jwt(token)


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
        # F-59（2026-09-03）：复用共享连接池 + Valkey 挂 fail-closed（401 非 500）——原每请求
        # from_url 建连且 r.exists 无保护，Valkey 抖动即 500 认证全瘫。fail-closed：登出黑名单
        # 安全敏感，Valkey 不可达时无法确认 token 未登出，应拒绝而非放行（放行会让已登出
        # token 复活——logout 无 PG 兜底，PG 只兜底 deactivate）。
        try:
            from src.web_api.redis_pool import redis_client
            if redis_client().exists(f"jwt:bl:{jti}"):
                raise HTTPException(401, "token 已登出")
        except HTTPException:
            raise
        except Exception as e:
            _logger.warning("登出黑名单检查失败（Valkey 不可达，fail-closed 拒绝）: %s", e)
            raise HTTPException(401, "认证服务暂不可用，请稍后重试")
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
    from datetime import datetime as _dt
    from src.web_api.redis_pool import redis_client
    remaining = payload["exp"] - int(_dt.utcnow().timestamp())
    if remaining > 0:
        try:
            redis_client().setex(f"jwt:bl:{jti}", remaining, "1")
        except Exception as e:
            # F-59（2026-09-03）：Valkey 不可达时降级——登出仍成功（客户端丢弃 token），
            # 黑名单未记录则 token 靠自然过期兜底，不阻塞登出流程。
            _logger.warning("登出黑名单写入失败（Valkey 不可达，token 靠自然过期）: %s", e)
            return False
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


def register_user(token: str, username: str, password: str, nickname: str = "") -> dict | None:
    """自助开通：凭 invite token 建用户（默认 Viewer）。批11：nickname 选填；email_verified 列已删（裁定③）。"""
    t = verify_token(token, "invite")
    if not t:
        return None
    try:
        uid = create_user(username, password, "viewer")
    except ValueError:
        return None  # 用户名已存在
    with get_conn() as conn:
        conn.execute("UPDATE users SET email=%s, nickname=LEFT(COALESCE(NULLIF(%s,''), nickname), 20) WHERE id=%s",
                     (t["email"], nickname, uid))   # 昵称 20 上限对齐 profile_update（盲审 P2-4）
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