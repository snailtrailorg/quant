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
JWT_SECRET = os.environ.get("JWT_SECRET", "quant-dev-secret-change-me")
if JWT_SECRET == "quant-dev-secret-change-me":
    _logger.warning("JWT_SECRET 使用默认值，生产环境请通过环境变量设置独立密钥")
JWT_ALGO = "HS256"
JWT_TTL_HOURS = 24

# ——— 权限矩阵（4 级：Admin/Trader/Analyst/Viewer）——

PERMISSIONS = {
    "viewer":  {"read"},
    "analyst": {"read", "strategy_control", "data_sync", "system_config"},   # 研究：策略/回测/数据同步/系统配置
    "trader":  {"read", "strategy_control", "halt", "trade", "live_trading_control"},  # 交易：策略启停/熔断/下单/实盘开关
    "admin":   {"read", "strategy_control", "data_sync", "halt", "resume", "trade", "live_trading_control",
                 "risk_rules", "account_keys", "user_mgmt", "system_config", "llm_config", "feishu_config"},
}


def require_role(*allowed: Role):
    """FastAPI 依赖：检查 JWT 角色是否在允许列表中。"""
    def checker(authorization: str = Header(...)):
        token = re.sub(r'^Bearer\s+', '', authorization, flags=re.IGNORECASE)
        payload = verify_jwt(token)
        role = payload.get("role", "viewer")
        if role not in allowed:
            raise HTTPException(403, f"角色 {role} 无权限，需 {allowed}")
        return payload
    return checker


def require_perm(perm: str):
    """FastAPI 依赖：检查 JWT 角色是否有指定权限。"""
    def checker(authorization: str = Header(...)):
        token = re.sub(r'^Bearer\s+', '', authorization, flags=re.IGNORECASE)
        payload = verify_jwt(token)
        role = payload.get("role", "viewer")
        perms = PERMISSIONS.get(role, set())
        if perm not in perms:
            raise HTTPException(403, f"角色 {role} 无 {perm} 权限")
        return payload
    return checker


# ——— JWT ———

def create_jwt(user_id: str, username: str, role: Role) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=JWT_TTL_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def verify_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token 过期")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "token 无效")


# ——— 用户管理（PG） ———

def init_users_table():
    """初始化用户表 + 审计表（表已在 migration 0001 创建，保留接口兼容，不再 DDL）。"""
    return


def hash_password(password: str) -> str:
    """bcrypt 哈希（生产级，带 salt）。"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def validate_password(password: str) -> None:
    """密码复杂度校验：≥8 位，需含字母和数字。不达标抛 ValueError（前端 register/change-password 同规则）。"""
    if not password or len(password) < 8:
        raise ValueError("密码至少 8 位")
    if not re.search(r"[A-Za-z]", password):
        raise ValueError("密码需包含字母")
    if not re.search(r"[0-9]", password):
        raise ValueError("密码需包含数字")


def guard_user_mutation(target_username: str, current_username: str, removes_admin: bool) -> None:
    """账户变更保护（DELETE / PUT user 共用，两条不变量，防两类锁定）：
    1. 不能动自己 —— 管理页不得删/改角色/禁用"自己"那行（自我锁定）；
    2. 不能移除最后一个启用的管理员 —— 任何使启用 admin 数降为 0 的操作都拒（管理锁定）。
    removes_admin：本次操作是否剥夺一个"启用 admin"身份（删/降级/禁用 且目标当前是启用 admin），由调用方算。
    """
    if target_username == current_username:
        raise HTTPException(400, "不能修改或删除当前登录的账户")
    if removes_admin:
        with get_conn() as conn:
            cur = conn.execute("SELECT count(*) FROM users WHERE role='admin' AND enabled=true")
            if cur.fetchone()[0] <= 1:
                raise HTTPException(400, "不能移除最后一个启用的管理员")


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


def authenticate(username: str, password: str) -> dict | None:
    """验证用户，返回 {id, username, role} 或 None。"""
    init_users_table()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, username, password_hash, role, enabled FROM users WHERE username=%s",
            (username,),
        )
        row = cur.fetchone()
        if not row or not row[4]:
            return None
        if not verify_password(password, row[2]):
            return None
        return {"id": row[0], "username": row[1], "role": row[3]}


def audit_log(actor: str, action: str, target: str = "", detail: str = "",
              old_value: str = "", new_value: str = ""):
    """写审计日志（含新旧值对比）。"""
    init_users_table()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (actor, action, target, detail, old_value, new_value) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (actor, action, target, detail, old_value, new_value),
        )
        conn.commit()


def ensure_default_admin():
    """确保有默认 admin 账号（首次启动）+ 旧 sha256 密码重置为 bcrypt。"""
    init_users_table()
    with get_conn() as conn:
        cur = conn.execute("SELECT password_hash FROM users WHERE username='admin'")
        row = cur.fetchone()
        if not row:
            create_user("admin", "admin123", "admin")
            return True
        # 旧 sha256 密码（非 $2b$ 开头）重置为 bcrypt（admin123）
        if not row[0].startswith("$2b$"):
            conn.execute("UPDATE users SET password_hash=%s WHERE username='admin'",
                         (hash_password("admin123"),))
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
    """校验 token（未用过 + 未过期）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, user_id, email, expires_at, used FROM user_tokens WHERE token=%s AND type=%s",
            (token, token_type))
        row = cur.fetchone()
    if not row or row[4]:  # used
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