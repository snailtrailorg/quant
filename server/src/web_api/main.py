"""Web 后端 · FastAPI 应用 + API 端点。

启动: uvicorn src.web_api.main:app --reload --port 8000
"""

from __future__ import annotations
from src.data_platform.db import get_conn
import os, json, logging, psycopg, redis, subprocess, uuid
import requests
import pandas as pd
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends, Header, Query, Body, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("web_api")

# Redis 连接池（各端点复用，避免每次请求新建连接）
_redis_pool = redis.ConnectionPool.from_url(
    os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True,
)
_redis_pool_feishu = redis.ConnectionPool.from_url(
    os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/4"),
    decode_responses=True,
)

from .auth import (
    create_jwt, authenticate, create_user, require_role, require_perm,
    audit_log, ensure_default_admin, init_users_table, PERMISSIONS,
    invite_user, register_user, forgot_password, reset_password, change_password, verify_token,
    validate_password, guard_user_mutation, soft_delete_user, guard_self_deactivate,
)
from .email_service import send_invite_email, send_password_reset_email, send_activation_email, queue_email, try_row
from .terms import get_terms, get_terms_items
from .errors import ApiError

app = FastAPI(title="量化交易平台 API", version="0.1.0")


@app.exception_handler(ApiError)
async def api_error_handler(request, exc: ApiError):
    """错误码化响应：detail(中文兜底) + 顶层 code（前端 err.<CODE> 本地化）。"""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})


from src.feishu_bot.router import router as feishu_router
app.include_router(feishu_router)

# ——— 头像静态服务（批次C）：挂 /api/static/avatars —— nginx 已代理 /api/，零额外配置同源可达 ———
import os as _os
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles as _StaticFiles
_AVATAR_DIR = _Path(__file__).resolve().parents[2] / "static" / "avatars"
_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/static", _StaticFiles(directory=str(_AVATAR_DIR.parent)), name="static")

# CORS（前端 Vue3 开发用）
# SD2（F-58）：CORS 白名单化。默认生产域名；本地 dev 走 vite 同源代理不受影响；
# 跨域开发场景用 CORS_ORIGINS 环境变量覆盖（逗号分隔）
_CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "https://quant.snailtrail.cc").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ——— 数据模型 ———

class LoginReq(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "viewer"

class StrategyConfig(BaseModel):
    id: str
    name: str
    type: str
    symbol: str
    adapter: str
    enabled: bool = True
    factors: list = []
    aggregator: dict = {}
    risk: dict = {}
    params: dict = {}

class InviteReq(BaseModel):
    email: str
    lang: str = "en"   # 邀请者操作界面语言（邮件语言跟随，未匹配回落 en）

class RegisterReq(BaseModel):
    token: str
    username: str
    password: str
    lang: str = "en"   # 注册者操作界面语言（开通邮件跟随）

class ForgotReq(BaseModel):
    email: str
    lang: str = "en"   # 请求者操作界面语言（重置邮件跟随）

class ResetReq(BaseModel):
    token: str
    new_password: str

class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str


# ——— 启动时初始化 ———

@app.on_event("startup")
def startup():
    init_users_table()
    if ensure_default_admin():
        print("✓ 创建默认 admin（admin/admin123，请改密码）")
    # 加载自定义因子（因子平台化）
    try:
        from src.strategy_framework.factor import load_factors_from_db
        loaded = load_factors_from_db()
        if loaded:
            print(f"✓ 加载自定义因子: {', '.join(loaded)}")
    except Exception as e:
        logger.warning("startup: 加载自定义因子失败（表可能未创建）: %s", e)


# ——— 标准暴露端（15-服务监控设计：k8s 探针约定 + Prometheus 格式）———

@app.get("/healthz")
@app.get("/health")   # 兼容旧路径
def healthz():
    """liveness：进程活着即 ok（不查依赖）。nginx/Zabbix 外部探活用。"""
    return {"status": "ok", "version": "0.1.0"}


@app.get("/readyz")
def readyz():
    """readiness：依赖可达才 200（PG + Valkey），不可达 503。部署闸门/流量入口用。
    依赖探测复用 health_monitor.collect（盲审 D：与 /metrics 同一口径，不另养第二套探测）。"""
    from src.health_monitor.collector import collect
    snap = collect()
    checks = {dep: ("ok" if ok else f"fail: {str(snap['deps'].get(f'{dep}_err', ''))[:60]}")
              for dep, ok in snap.get("deps", {}).items() if isinstance(ok, bool)}
    ok = all(v == "ok" for v in checks.values()) if checks else False
    if not ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
    return {"status": "ok", "checks": checks}


@app.get("/metrics")
def metrics():
    """Prometheus 文本格式（text/plain; version=0.0.4）——业界交换标准。

    Zabbix HTTP agent（Prometheus pattern 预处理）/ Prometheus / Grafana 通吃。
    Phase 2 Zabbix 落地时在 nginx 层限源（只许 NAS Zabbix/内网）。
    """
    from src.health_monitor.collector import collect, render_prometheus
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(render_prometheus(collect()), media_type="text/plain; version=0.0.4; charset=utf-8")


# --- 系统配置（system_config，admin 可改，部分项支持动态生效） ---

def _adjust_celery_concurrency(new_value: int) -> dict:
    """动态调整 Celery worker 并发度（via broker 发 pool_grow/shrink 控制命令）。

    Web API 进程通过 Celery app 连同一个 Valkey broker，control 命令经 broker 推到 worker。
    """
    try:
        from src.scheduler.app import app as celery_app
        insp = celery_app.control.inspect()
        stats = insp.stats() or {}
        if not stats:
            return {"applied": False, "reason": "无 worker 在线（DB 已更新，下次 worker 启动生效）"}
        results = {}
        for worker_name, info in stats.items():
            current = info.get("pool", {}).get("max-concurrency", 2)
            delta = new_value - current
            if delta > 0:
                celery_app.control.pool_grow(delta, destination=[worker_name])
                results[worker_name] = f"{current} -> {new_value} (grow {delta})"
            elif delta < 0:
                celery_app.control.pool_shrink(-delta, destination=[worker_name])
                results[worker_name] = f"{current} -> {new_value} (shrink {-delta})"
            else:
                results[worker_name] = f"{current} (无变化)"
        return {"applied": True, "workers": results}
    except Exception as e:
        return {"applied": False, "reason": f"动态调整失败（DB 已更新，下次 worker 启动生效）: {e}"}


@app.get("/api/smtp-config")
def smtp_config_api(payload: dict = Depends(require_perm("user_mgmt"))):
    """邮件发信配置（整组读取；password 不回传明文，只回 password_set 标记）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT key, value FROM system_config WHERE key LIKE 'smtp_%'")
        cfg = {k: v for k, v in cur.fetchall()}
    return {
        "host": cfg.get("smtp_host", ""),
        "port": cfg.get("smtp_port", "587"),
        "security": cfg.get("smtp_security", "auto"),
        "username": cfg.get("smtp_username", ""),
        "password_set": bool(cfg.get("smtp_password")),
        "from": cfg.get("smtp_from", ""),
    }


@app.put("/api/smtp-config")
def smtp_config_save_api(body: dict = Body(...),
                         payload: dict = Depends(require_perm("user_mgmt"))):
    """邮件发信配置整组保存。password 留空=保持不变；security ∈ auto/ssl/starttls。"""
    security = str(body.get("security", "auto")).strip() or "auto"
    if security not in ("auto", "ssl", "starttls"):
        raise ApiError(400, "SMTP_SECURITY_INVALID", "security 需为 auto / ssl / starttls")
    port = str(body.get("port", "587")).strip() or "587"
    try:
        int(port)
    except ValueError:
        raise ApiError(400, "SMTP_PORT_INVALID", "port 需为数字")
    from .crypto_utils import encrypt
    values = {
        "smtp_host": str(body.get("host", "")).strip(),
        "smtp_port": port,
        "smtp_security": security,
        "smtp_username": str(body.get("username", "")).strip(),
        "smtp_from": str(body.get("from", "")).strip(),
    }
    with get_conn() as conn:
        for k, v in values.items():
            conn.execute(
                "INSERT INTO system_config (key, value, value_type, description) "
                "VALUES (%s, %s, 'text', '') ON CONFLICT (key) DO UPDATE SET value=%s, updated_at=now()",
                (k, v, v))
        pwd = str(body.get("password", "") or "").strip()
        if pwd:  # 留空=不变
            conn.execute(
                "INSERT INTO system_config (key, value, value_type, description) "
                "VALUES ('smtp_password', %s, 'password', 'SMTP 密码（加密）') "
                "ON CONFLICT (key) DO UPDATE SET value=%s, updated_at=now()",
                (encrypt(pwd), encrypt(pwd)))
        conn.commit()
    audit_log(payload["username"], "smtp_config_save",
              f"host={values['smtp_host']} port={port} security={security} pwd={'***' if pwd else 'unchanged'}")
    return {"ok": True}


@app.post("/api/email/test")
async def email_test_api(body: dict = Body(...), request: Request = None,
                         background_tasks: BackgroundTasks = None,
                         payload: dict = Depends(require_perm("user_mgmt"))):
    """发送测试邮件（走发件箱，立即可在 Logs 页看结果；失败自动指数退避重试）。"""
    to = str(body.get("to", "")).strip()
    if not to or "@" not in to:
        raise ApiError(400, "EMAIL_INVALID", "请填有效收件邮箱")
    subject = "测试邮件 · 人工智能开发学习平台"
    html = ("<html><body style='font-family:sans-serif'><h3>✅ 测试邮件</h3>"
            "<p>这是一封配置验证邮件。收到即表示 SMTP 发信配置正确。</p></body></html>")
    outbox_id = queue_email(to, subject, html)
    background_tasks.add_task(try_row, outbox_id)
    audit_log(payload["username"], "email_test", to)
    return {"queued": True, "outbox_id": outbox_id}


@app.get("/api/system-config")
def list_system_config(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列系统配置（viewer+ 只读）。password 型不回传明文，返回空值 + has_value 标记。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT key, value, value_type, description, updated_at, updated_by "
            "FROM system_config ORDER BY key")
        rows = cur.fetchall()
    items = []
    for r in rows:
        value = r[1]
        if r[2] == "password":
            items.append({"key": r[0], "value": "", "has_value": bool(value),
                          "value_type": r[2], "description": r[3],
                          "updated_at": str(r[4]) if r[4] else None, "updated_by": r[5]})
        else:
            items.append({"key": r[0], "value": value, "value_type": r[2], "description": r[3],
                          "updated_at": str(r[4]) if r[4] else None, "updated_by": r[5]})
    return {"items": items}


@app.put("/api/system-config/{key}")
def update_system_config(key: str, body: dict = Body(...),
                          payload: dict = Depends(require_role("admin"))):
    """更新系统配置（仅 admin）。部分 key 支持动态生效（如 celery_concurrency）。
    password 型：留空=不修改（400 提示），非空=Fernet 加密存储。"""
    value = body.get("value")
    if value is None:
        raise ApiError(400, "CONFIG_VALUE_INVALID", "缺 value 字段")
    with get_conn() as conn:
        cur = conn.execute("SELECT value_type FROM system_config WHERE key=%s", (key,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "CONFIG_KEY_NOT_FOUND", f"系统配置 {key} 不存在")
        value_type = row[0]
        # 类型校验 + 规范化
        if value_type == "int":
            try: value = str(int(value))
            except Exception: raise ApiError(400, "CONFIG_VALUE_INVALID", f"{key} 需 int 值")
        elif value_type == "float":
            try: value = str(float(value))
            except Exception: raise ApiError(400, "CONFIG_VALUE_INVALID", f"{key} 需 float 值")
        elif value_type == "bool":
            value = "true" if value in (True, "true", "True", "1", 1) else "false"
        elif value_type == "json":
            try: value = json.dumps(value) if not isinstance(value, str) else value
            except Exception: pass
        elif value_type == "password":
            value = str(value).strip()
            if not value:
                raise ApiError(400, "CONFIG_PASSWORD_EMPTY", "password 型留空=不修改；如需更换请填新值")
            from .crypto_utils import encrypt
            value = encrypt(value)
        conn.execute(
            "UPDATE system_config SET value=%s, updated_at=now(), updated_by=%s WHERE key=%s",
            (str(value), payload["username"], key))
        conn.commit()
    audit_log(payload["username"], "update_system_config", key, "(password)" if value_type == "password" else str(value))

    # 动态生效：celery_concurrency 即时 pool_grow/shrink
    dynamic_result = None
    if key == "celery_concurrency":
        dynamic_result = _adjust_celery_concurrency(int(value))
    shown = "(password updated)" if value_type == "password" else value
    return {"key": key, "value": shown, "dynamic": dynamic_result}


@app.get("/api/system-config/{key}")
def get_system_config(key: str, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """取单个系统配置。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT value, value_type, description FROM system_config WHERE key=%s", (key,))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "CONFIG_KEY_NOT_FOUND", f"系统配置 {key} 不存在")
    return {"key": key, "value": r[0], "value_type": r[1], "description": r[2]}


# ——— 认证 ———

@app.post("/api/auth/login")
def login(req: LoginReq, request: Request):
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


@app.get("/api/auth/me")
def me(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    nickname, avatar_url = payload["username"], None
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT nickname, avatar_url FROM users WHERE id=%s", (payload["sub"],))
            r = cur.fetchone()
        if r:
            nickname, avatar_url = r[0] or payload["username"], r[1]
    except Exception:
        pass
    return {"user_id": payload["sub"], "username": payload["username"], "role": payload["role"],
            "nickname": nickname, "avatar_url": avatar_url,
            "permissions": list(PERMISSIONS.get(payload["role"], set()))}


@app.post("/api/auth/logout")
def logout(authorization: str = Header(...),
           payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """登出：token 加入黑名单立即失效（A4）+ 审计。"""
    from .auth import revoke_jwt
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


@app.get("/api/user/profile")
def profile_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """个人中心：当前用户资料（批次C）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT username, nickname, role, avatar_url, email FROM users WHERE id=%s", (payload["sub"],))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
    return {"username": r[0], "nickname": r[1], "role": r[2], "avatar_url": r[3], "email": r[4]}


@app.put("/api/user/profile")
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


@app.post("/api/user/avatar")
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


@app.post("/api/user/deactivate")
def deactivate_api(authorization: str = Header(...),
                   payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """自助注销（批次D）：软删+脱敏+token 拉黑。末位 admin 不可注销自己（该路径真实可达）。"""
    guard_self_deactivate(int(payload["sub"]))
    soft_delete_user(int(payload["sub"]))
    from .auth import revoke_jwt
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    revoke_jwt(token)
    _af = _AVATAR_DIR / f"user_{payload['sub']}.jpg"
    if _af.exists():
        _af.unlink()
    audit_log(payload["username"], "self_deactivate")
    return {"ok": True}


@app.get("/api/invites")
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


@app.post("/api/invites/{tid}/revoke")
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


@app.post("/api/auth/invite")
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


@app.get("/api/auth/invite/verify")
def verify_invite_token(token: str):
    """验证 invite token 有效性（前端开通页用）。"""
    t = verify_token(token, "invite")
    if not t:
        raise ApiError(400, "TOKEN_INVALID_OR_EXPIRED", "token 无效或已过期")
    return {"valid": True, "email": t["email"]}


@app.post("/api/auth/register")
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


@app.get("/api/terms")
def terms_api():
    """平台使用条款（公开，注册页 + 开通邮件共用单一源）。
    返回 items: [{lang, name, body}] —— N 语言注册表驱动，前端遍历展示不感知具体语言。"""
    return {"items": get_terms_items()}


@app.post("/api/auth/forgot-password")
async def forgot_password_api(req: ForgotReq, request: Request, background_tasks: BackgroundTasks):
    """找回密码：发重置邮件（后台发送，SMTP 慢/失败不阻塞接口；不泄露 email 是否存在）。"""
    token = forgot_password(req.email)
    if not token:
        return {"status": "sent"}  # email 不存在也返回 sent（防枚举）
    background_tasks.add_task(send_password_reset_email, req.email, token, _request_base(request), req.lang)
    return {"status": "sent"}


@app.post("/api/auth/reset-password")
def reset_password_api(req: ResetReq):
    """凭 reset token 重置密码。"""
    validate_password(req.new_password)  # 不达标直接抛 ApiError(含错误码)
    ok = reset_password(req.token, req.new_password)
    if not ok:
        raise ApiError(400, "TOKEN_INVALID_OR_EXPIRED", "token 无效或已用/过期")
    return {"status": "reset"}


@app.post("/api/auth/change-password")
def change_password_api(req: ChangePwdReq, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """改密码：需旧密码验证。"""
    validate_password(req.new_password)  # 不达标直接抛 ApiError(含错误码)
    ok = change_password(int(payload["sub"]), req.old_password, req.new_password)
    if not ok:
        raise ApiError(400, "OLD_PASSWORD_WRONG", "旧密码错误")
    audit_log(payload["username"], "change_password")
    return {"status": "changed"}


# ——— 用户管理（Admin） ———

@app.post("/api/user")
def create_user_api(req: UserCreate, payload: dict = Depends(require_perm("user_mgmt"))):
    try:
        uid = create_user(req.username, req.password, req.role)
        audit_log(payload["username"], "create_user", req.username, f"role={req.role}")
        return {"id": uid, "username": req.username, "role": req.role}
    except ValueError as e:
        raise ApiError(409, "USERNAME_EXISTS", str(e))


@app.get("/api/user")
def list_users(payload: dict = Depends(require_perm("user_mgmt"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, username, nickname, role, enabled, email, email_verified, created_at, "
                           "last_login_at, deleted_at FROM users ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "username": r[1], "nickname": r[2], "role": r[3],
             "enabled": r[4] and not r[9], "deactivated": bool(r[9]),
             "email": r[5], "email_verified": r[6], "created_at": str(r[7])[:19],
             "last_login_at": str(r[8])[:19] if r[8] else None} for r in rows]


@app.put("/api/user/{uid}")
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


@app.delete("/api/user/{uid}")
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


# --- 策略管理（DB 驱动） ---

@app.get("/api/strategy")
def list_strategies(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列策略配置（从 DB 读）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, type, symbol, adapter, enabled, factors, aggregator, risk, params, backtest_verified FROM strategy_config ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "symbol": r[3], "adapter": r[4],
             "enabled": r[5], "factors": r[6], "aggregator": r[7], "risk": r[8], "params": r[9], "backtest_verified": r[10]} for r in rows]


@app.post("/api/strategy")
def create_strategy(req: StrategyConfig, payload: dict = Depends(require_perm("strategy_control"))):
    """新建策略配置。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO strategy_config (id, name, type, symbol, adapter, enabled, factors, aggregator, risk, params) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (req.id, req.name, req.type, req.symbol, req.adapter, req.enabled,
             json.dumps(req.factors), json.dumps(req.aggregator), json.dumps(req.risk), json.dumps(req.params)))
        conn.commit()
    audit_log(payload["username"], "create_strategy", req.id, json.dumps({"name": req.name}))
    return {"id": req.id, "status": "created"}


@app.put("/api/strategy/{sid}")
def update_strategy(sid: str, req: StrategyConfig, payload: dict = Depends(require_perm("strategy_control"))):
    """更新策略配置（含因子校验；Python 模式跳过因子校验）。"""
    # Python 模式（#15）跳过因子校验
    if req.params.get("mode") != "python":
        from src.strategy_framework.factor import validate_strategy_factors
        v = validate_strategy_factors(req.symbol, req.factors)
        if not v["valid"]:
            raise HTTPException(400, f"因子不兼容: {v['message']}")
    with get_conn() as conn:
        cur = conn.execute("SELECT factors, aggregator FROM strategy_config WHERE id=%s", (sid,))
        old = cur.fetchone()
        conn.execute(
            "UPDATE strategy_config SET name=%s, type=%s, symbol=%s, adapter=%s, enabled=%s, "
            "factors=%s, aggregator=%s, risk=%s, params=%s, updated_at=now() WHERE id=%s",
            (req.name, req.type, req.symbol, req.adapter, req.enabled,
             json.dumps(req.factors), json.dumps(req.aggregator), json.dumps(req.risk), json.dumps(req.params), sid))
        conn.commit()
    audit_log(payload["username"], "update_strategy", sid,
              old_value=json.dumps({"factors": old[0], "aggregator": old[1]}) if old else "",
              new_value=json.dumps({"factors": req.factors, "aggregator": req.aggregator}))
    return {"id": sid, "status": "updated"}


@app.post("/api/strategy/{sid}/start")
def start_strategy(sid: str, payload: dict = Depends(require_perm("strategy_control"))):
    """启动策略。未通过回测验证禁止实盘（EXE-003）。策略必须绑定标的或标的池（F-POOL-003）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT backtest_verified, symbol, params FROM strategy_config WHERE id=%s", (sid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "策略不存在")
        if not row[0]:
            raise HTTPException(403, "策略未通过回测验证，禁止实盘。请先运行回测。")
        symbol, params_raw = row[1], row[2]
        params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or {})
        # F-POOL-003：策略必须绑定标的或标的池
        if not symbol and not params.get("pool_id"):
            raise HTTPException(400, "策略未绑定标的或标的池，禁止启动。请在策略编辑页设置 symbol 或 pool_id。")
        conn.execute("UPDATE strategy_config SET enabled=true WHERE id=%s AND enabled=false AND backtest_verified=true", (sid,))
        conn.commit()
    audit_log(payload["username"], "strategy_start", sid)
    try:
        subprocess.run(["systemctl", "start", f"quant-strategy@{sid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("start_strategy: systemctl start quant-strategy@%s 失败", sid, exc_info=True)
    return {"id": sid, "status": "running"}


@app.post("/api/strategy/{sid}/stop")
def stop_strategy(sid: str, payload: dict = Depends(require_perm("strategy_control"))):
    with get_conn() as conn:
        conn.execute("UPDATE strategy_config SET enabled=false WHERE id=%s", (sid,))
        conn.commit()
    audit_log(payload["username"], "strategy_stop", sid)
    try:
        subprocess.run(["systemctl", "stop", f"quant-strategy@{sid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("stop_strategy: systemctl stop quant-strategy@%s 失败", sid, exc_info=True)
    return {"id": sid, "status": "stopped"}


@app.post("/api/strategy/{sid}/verify")
def verify_strategy(sid: str, body: dict = Body(default={}), payload: dict = Depends(require_perm("strategy_control"))):
    """标记策略已通过回测验证。

    SD2（F-44）：回测门禁需真实证据——须提供属于该策略且状态 done 的 run_id，
    或该策略存在至少一条已完成回测；否则拒绝。
    """
    run_id = body.get("run_id")
    with get_conn() as conn:
        if run_id is not None:
            cur = conn.execute(
                "SELECT status FROM backtest_runs WHERE id=%s AND strategy_config_id=%s", (run_id, sid))
            row = cur.fetchone()
            if not row or row[0] != "done":
                raise HTTPException(400, f"回测证据无效: run_id={run_id}（须属于该策略且状态 done）")
        else:
            cur = conn.execute(
                "SELECT COUNT(*) FROM backtest_runs WHERE strategy_config_id=%s AND status='done'", (sid,))
            if cur.fetchone()[0] == 0:
                raise HTTPException(403, "该策略无已完成回测，禁止标记验证（需真实回测证据，F-44）")
        conn.execute("UPDATE strategy_config SET backtest_verified=true WHERE id=%s", (sid,))
        conn.commit()
    audit_log(payload["username"], "verify_strategy", sid, detail="回测验证通过")
    return {"id": sid, "backtest_verified": True}


@app.post("/api/strategy/validate-python")
def validate_python_code(code: dict = Body(...), payload: dict = Depends(require_role("analyst", "trader", "admin"))):
    """校验 Python 策略代码：语法检查 + AST 安全校验（#15）。"""
    from src.strategy_framework.strategy import _check_ast_blacklist
    code_str = code.get("code", "")
    forbidden = _check_ast_blacklist(code_str)
    if forbidden:
        return {"valid": False, "error": forbidden}
    return {"valid": True}


@app.post("/api/strategy/validate-params")
def validate_params_api(body: dict = Body(...),
                        payload: dict = Depends(require_role("analyst", "trader", "admin"))):
    """校验策略参数定义 + 参数值（parameter_defs 系统）。"""
    from src.strategy_framework.strategy import (
        validate_parameter_defs, validate_params_against_defs, build_default_params
    )
    defs = body.get("parameter_defs", [])
    params = body.get("params", {})
    err = validate_parameter_defs(defs)
    if err:
        return {"valid": False, "error": err}
    err = validate_params_against_defs(params, defs)
    if err:
        return {"valid": False, "error": err}
    return {"valid": True, "defaults": build_default_params(defs)}


# --- 实盘任务（live_task，策略与标的分离） ---

@app.get("/api/live-task")
def list_live_tasks(status: str | None = None,
                    payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列实盘任务。"""
    with get_conn() as conn:
        if status:
            cur = conn.execute(
                "SELECT id, name, strategy_id, symbol, params, status, account_id, initial_capital, created_at "
                "FROM live_task WHERE status=%s ORDER BY id DESC", (status,))
        else:
            cur = conn.execute(
                "SELECT id, name, strategy_id, symbol, params, status, account_id, initial_capital, created_at "
                "FROM live_task ORDER BY id DESC")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "strategy_id": r[2], "symbol": r[3],
             "params": json.loads(r[4]) if isinstance(r[4], str) else (r[4] or {}),
             "status": r[5], "account_id": r[6], "initial_capital": float(r[7]) if r[7] else None,
             "created_at": str(r[8]) if r[8] else None} for r in rows]


@app.post("/api/live-task")
def create_live_task(body: dict = Body(...),
                     payload: dict = Depends(require_perm("strategy_control"))):
    """创建实盘任务：选策略+标的+任务参数值。创建时构建 strategy_snapshot。"""
    from src.strategy_framework.strategy import (
        validate_parameter_defs, validate_params_against_defs, build_default_params
    )
    name = body.get("name", "")
    strategy_id = body.get("strategy_id", "")
    symbol = body.get("symbol", "")
    params = body.get("params", {})
    account_id = body.get("account_id")
    initial_capital = body.get("initial_capital", 1000000)

    if not name or not strategy_id or not symbol:
        raise HTTPException(400, "name/strategy_id/symbol 必填")

    # 读策略配置
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, name, type, symbol, adapter, enabled, factors, aggregator, risk, params, backtest_verified "
            "FROM strategy_config WHERE id=%s", (strategy_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"策略 {strategy_id} 不存在")
    if not row[10]:
        raise HTTPException(403, "策略未通过回测验证，禁止实盘")

    sc_params = json.loads(row[9]) if isinstance(row[9], str) else (row[9] or {})
    defs = sc_params.get("parameter_defs", [])

    # 校验参数定义
    err = validate_parameter_defs(defs)
    if err:
        raise HTTPException(400, f"策略参数定义错误: {err}")

    # 合并默认值 + 用户传入参数
    merged_params = {**build_default_params(defs), **params}
    err = validate_params_against_defs(merged_params, defs)
    if err:
        raise HTTPException(400, f"参数值错误: {err}")

    # 构建策略快照（创建时固化，后续改策略不影响）
    strategy_snapshot = {
        "id": row[0], "name": row[1], "type": row[2],
        "adapter": row[4], "factors": row[6] if isinstance(row[6], list) else json.loads(row[6] or "[]"),
        "aggregator": row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}"),
        "risk": row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}"),
        "params": sc_params,  # 含 mode/python_code/parameter_defs
    }

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO live_task (name, strategy_id, symbol, params, strategy_snapshot, status, "
            "account_id, initial_capital) VALUES (%s,%s,%s,%s,%s,'pending',%s,%s) RETURNING id",
            (name, strategy_id, symbol, json.dumps(merged_params), json.dumps(strategy_snapshot),
             account_id, initial_capital))
        task_id = cur.fetchone()[0]
        conn.commit()
    audit_log(payload["username"], "create_live_task", f"task {task_id} strategy={strategy_id} symbol={symbol}")
    return {"id": task_id, "status": "pending"}


@app.post("/api/live-task/{tid}/start")
def start_live_task(tid: int, payload: dict = Depends(require_perm("strategy_control"))):
    """启动实盘任务。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT status, strategy_id FROM live_task WHERE id=%s", (tid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "实盘任务不存在")
        conn.execute("UPDATE live_task SET status='running', updated_at=now() WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "start_live_task", f"task {tid}")
    try:
        subprocess.run(["systemctl", "start", f"quant-live-task@{tid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("start_live_task: systemctl start quant-live-task@%s 失败", tid, exc_info=True)
    return {"id": tid, "status": "running"}


@app.post("/api/live-task/{tid}/stop")
def stop_live_task(tid: int, payload: dict = Depends(require_perm("strategy_control"))):
    """停止实盘任务。"""
    with get_conn() as conn:
        conn.execute("UPDATE live_task SET status='stopped', updated_at=now() WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "stop_live_task", f"task {tid}")
    try:
        subprocess.run(["systemctl", "stop", f"quant-live-task@{tid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("stop_live_task: systemctl stop quant-live-task@%s 失败", tid, exc_info=True)
    return {"id": tid, "status": "stopped"}


@app.delete("/api/live-task/{tid}")
def delete_live_task(tid: int, payload: dict = Depends(require_perm("strategy_control"))):
    """删除实盘任务（仅 stopped/error 可删）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT status FROM live_task WHERE id=%s", (tid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "实盘任务不存在")
        if row[0] == "running":
            raise HTTPException(400, "运行中的任务不可删除，请先停止")
        conn.execute("DELETE FROM live_task WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "delete_live_task", f"task {tid}")
    return {"ok": True}


# ——— 持仓/盈亏 ———

@app.get("/api/position")
def get_position(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """当前持仓（account_snapshot 总资产 + trade_log 累计持仓，#6）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM account_snapshot LIMIT 1")
        except Exception:
            logger.warning("get_position: account_snapshot 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 1")
        snap = cur.fetchone()
        try:
            conn.execute("SELECT 1 FROM trade_log LIMIT 1")
        except Exception:
            logger.warning("get_position: trade_log 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT symbol, COALESCE(SUM(CASE WHEN action='BUY' THEN volume ELSE -volume END),0) FROM trade_log GROUP BY symbol")
        positions = [{"symbol": r[0], "volume": int(r[1])} for r in cur.fetchall() if r[1] and r[1] != 0]
    total_value = float(snap[0]) if snap else 0
    initial = float(snap[2]) if snap and snap[2] is not None else 1000000
    total_pnl = (total_value - initial) if snap else 0
    return {"positions": positions, "total_value": total_value, "total_pnl": total_pnl, "total_pnl_pct": round(total_pnl/initial*100, 2) if initial else 0}


@app.get("/api/pnl")
def get_pnl(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """盈亏曲线（account_snapshot 时间序列，#6）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM account_snapshot LIMIT 1")
        except Exception:
            logger.warning("get_pnl: account_snapshot 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT ts, total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 90")
        rows = cur.fetchall()
    curve = [{"ts": str(r[0])[:19], "value": float(r[1]) if r[1] else 0, "daily_pnl": float(r[2]) if r[2] else 0} for r in reversed(rows)]
    today_pnl = curve[-1]["daily_pnl"] if curve else 0
    initial = float(rows[0][3]) if rows and rows[0][3] is not None else 1000000
    total_pnl = (curve[-1]["value"] - initial) if curve else 0
    return {"curve": curve, "today_pnl": today_pnl, "total_pnl": total_pnl, "total_pnl_pct": round(total_pnl/initial*100, 2)}


@app.get("/api/orders")
def get_orders(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """订单记录（order_log 最近 100，#6）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM order_log LIMIT 1")
        except Exception:
            logger.warning("get_orders: order_log 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT ts, strategy_id, symbol, action, volume, price, status FROM order_log ORDER BY ts DESC LIMIT 100")
        rows = cur.fetchall()
    return {"orders": [{"ts": str(r[0])[:19], "strategy_id": r[1], "symbol": r[2], "action": r[3], "volume": r[4], "price": float(r[5]) if r[5] else 0, "status": r[6]} for r in rows], "total": len(rows)}


@app.get("/api/dashboard")
def get_dashboard(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """Dashboard 量化指标（account_snapshot + 回测绩效，#10）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM account_snapshot LIMIT 1")
        except Exception:
            logger.warning("get_dashboard: account_snapshot 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 1")
        snap = cur.fetchone()
        cur = conn.execute("SELECT COUNT(*) FROM backtest_runs WHERE status='done'")
        bt = cur.fetchone()
    total_value = float(snap[0]) if snap else 0
    initial = float(snap[2]) if snap and snap[2] is not None else 1000000
    total_pnl = (total_value - initial) if snap else 0
    return {"total_value": total_value, "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl / initial * 100, 2) if (snap and initial) else 0,
            "daily_pnl": float(snap[1]) if snap else 0, "backtest_count": bt[0]}


# ——— 账户管理（Admin） ———

@app.get("/api/account")
def list_accounts(payload: dict = Depends(require_perm("account_keys"))):
    """列券商/交易所账户（密钥不返回明文）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM accounts LIMIT 1")
        except Exception:
            logger.warning("list_accounts: accounts 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT id, name, exchange, api_key_hint, enabled, created_at FROM accounts ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "exchange": r[2], "api_key_hint": r[3],
             "enabled": r[4], "created_at": str(r[5])} for r in rows]


@app.get("/api/account/{aid}")
def get_account(aid: int, payload: dict = Depends(require_perm("account_keys"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, exchange, api_key_hint, enabled, created_at FROM accounts WHERE id=%s", (aid,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "账户不存在")
    return {"id": row[0], "name": row[1], "exchange": row[2], "api_key_hint": row[3], "enabled": row[4]}


@app.post("/api/account")
def create_account(req: dict = Body(...), payload: dict = Depends(require_perm("account_keys"))):
    """P4-5 创建账户。"""
    with get_conn() as conn:
        k = (req.get("name", ""), req.get("exchange", ""), req.get("api_key_hint", ""), req.get("enabled", True))
        cur = conn.execute("INSERT INTO accounts (name, exchange, api_key_hint, enabled) VALUES (%s,%s,%s,%s) RETURNING id", k)
        conn.commit()
        return {"id": cur.fetchone()[0]}


@app.put("/api/account/{aid}")
def update_account(aid: int, req: dict = Body(...), payload: dict = Depends(require_perm("account_keys"))):
    """P4-5 更新账户。"""
    with get_conn() as conn:
        for k in ("name", "exchange", "api_key_hint", "enabled"):
            if k in req:
                conn.execute(f"UPDATE accounts SET {k}=%s WHERE id=%s", (req[k], aid))
        conn.commit()
    return {"ok": True}


@app.delete("/api/account/{aid}")
def delete_account(aid: int, payload: dict = Depends(require_perm("account_keys"))):
    """P4-5 删除账户。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id=%s", (aid,))
        conn.commit()
    return {"ok": True}


# ——— 日志/告警 ———

class LogAnalyzeReq(BaseModel):
    logs: list[dict] | None = None
    task_id: str | None = None


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


@app.post("/api/log/analyze")
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


@app.get("/api/log")
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


@app.get("/api/email-outbox")
def email_outbox_api(payload: dict = Depends(require_perm("user_mgmt"))):
    """发件箱状态（持久化 + 指数退避重发）：pending 重发中 / sent 已发 / failed 重试耗尽。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, to_email, subject, status, attempts, next_attempt_at, last_error, created_at, sent_at "
            "FROM email_outbox ORDER BY id DESC LIMIT 50")
        rows = cur.fetchall()
    return {"items": [{
        "id": r[0], "to": r[1], "subject": r[2], "status": r[3], "attempts": r[4],
        "next_attempt_at": str(r[5])[:19] if r[5] else None,
        "last_error": r[6], "created_at": str(r[7])[:19], "sent_at": str(r[8])[:19] if r[8] else None,
    } for r in rows]}


@app.get("/api/notifications")
def notifications_api(status: str = "active", limit: int = 50,
                      payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """通知中心（站内铃铛/通知历史共用）。按当前角色过滤可见类别（email→admin 等）。"""
    from src.alert_notify import visible_categories
    cats = visible_categories(payload.get("role", "viewer"))
    if not cats:
        return {"items": [], "count": 0}
    cond = "" if status == "all" else "AND status=%s"
    params = [cats]
    if status != "all":
        params.append(status)
    params.append(min(limit, 200))
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, level, category, title, body, source_ref, status, created_at, acked_at "
            f"FROM notifications WHERE category = ANY(%s) {cond} "
            "ORDER BY id DESC LIMIT %s", tuple(params))
        rows = cur.fetchall()
        cur2 = conn.execute(
            "SELECT count(*) FROM notifications WHERE category = ANY(%s) AND status='active'",
            (cats,))
        active_count = cur2.fetchone()[0]
    return {
        "items": [{
            "id": r[0], "level": r[1], "category": r[2], "title": r[3], "body": r[4],
            "source_ref": r[5], "status": r[6],
            "created_at": str(r[7])[:19] if r[7] else "",
            "acked_at": str(r[8])[:19] if r[8] else None,
        } for r in rows],
        "count": active_count,
    }


@app.post("/api/notifications/ack-all")
def notifications_ack_all(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """全部确认：当前角色可见类别的 active → acked。"""
    from src.alert_notify import visible_categories
    cats = visible_categories(payload.get("role", "viewer"))
    if not cats:
        return {"acked": 0}
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE notifications SET status='acked', acked_by=%s, acked_at=now() "
            "WHERE category = ANY(%s) AND status='active'",
            (payload.get("username", ""), cats))
        conn.commit()
    audit_log(payload["username"], "notifications_ack_all", f"n={cur.rowcount}")
    return {"acked": cur.rowcount}


# ——— 自然语言查询 ———

class ChatReq(BaseModel):
    message: str


@app.post("/api/chat")
def chat(req: ChatReq, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """自然语言查询 → LLM 网关（只读工具直执闭环，P1-4）。"""
    try:
        from src.llm_gateway import gateway
        from src.llm_gateway.gateway import READ_TOOLS
        messages = [{"role": "user", "content": req.message}]
        resp = None
        for _ in range(3):  # max 3 轮工具调用
            resp = gateway.chat(
                messages=messages, tools=READ_TOOLS, role=payload["role"],
                timeout=30, retries=0, caller="web_chat",
            )
            if resp is None:
                return {"reply": "LLM 返回空", "usage": {}}
            if not resp.tool_calls:
                break
            # 执行工具 + 结果回填（P1-4 闭环）
            messages.append({"role": "assistant", "content": resp.content or ""})
            for tc in resp.tool_calls:
                result = _execute_readonly_tool(tc["name"], tc.get("arguments", "{}"))
                messages.append({"role": "user", "content": f"[工具 {tc['name']} 结果] {result}"})
        return {"reply": resp.content or "（LLM 无响应，请检查 API key）", "usage": resp.usage}
    except Exception as e:
        return {"reply": f"（LLM 暂不可用: {e}）", "usage": {}}


def _execute_readonly_tool(tool_name: str, args: str) -> str:
    """执行只读工具，返回结果文本（P1-4 LLM 工具闭环）。"""
    try:
        params = json.loads(args) if isinstance(args, str) else (args or {})
    except Exception:
        params = {}
    try:
        if tool_name == "query_risk_state":
            from src.risk_control import RiskControl
            rc = RiskControl.get()
            return f"熔断: {rc.is_halted()}, 原因: {rc.halt_reason()}"
        elif tool_name == "get_astock_analysis":
            from src.astock_analysis import DailySelectionEngine
            results = DailySelectionEngine(top_n=5).run()
            return str([{"symbol": r.symbol, "rating": r.rating, "score": r.score} for r in results])
        elif tool_name == "query_position":
            with get_conn() as conn:
                cur = conn.execute("SELECT total_value FROM account_snapshot ORDER BY ts DESC LIMIT 1")
                row = cur.fetchone()
            return f"总资产: {float(row[0]) if row else 0}"
        elif tool_name == "query_pnl":
            with get_conn() as conn:
                cur = conn.execute("SELECT total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 1")
                row = cur.fetchone()
            if row:
                return f"总资产: {float(row[0])}, 今日盈亏: {float(row[1])}, 初始资金: {float(row[2])}"
            return "无盈亏数据"
        elif tool_name == "query_strategy_status":
            with get_conn() as conn:
                cur = conn.execute("SELECT id, name, enabled FROM strategy_config ORDER BY id")
                rows = cur.fetchall()
            return str([{"id": r[0], "name": r[1], "enabled": r[2]} for r in rows])
        return f"工具 {tool_name} 未实现"
    except Exception as e:
        return f"工具执行失败: {e}"


# ——— A 股分析 ———

@app.get("/api/astock/selection")
def astock_selection(date: str = "", payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """当日选股结果。"""
    from src.astock_analysis import DailySelectionEngine
    trade_date = date or __import__("datetime").date.today().strftime("%Y%m%d")
    engine = DailySelectionEngine(top_n=20)
    results = engine.run(trade_date=trade_date)
    return [{"symbol": r.symbol, "vt_symbol": r.vt_symbol, "score": r.score,
             "rating": r.rating, "support": r.support, "resistance": r.resistance,
             "conclusion": r.conclusion} for r in results]


# ——— 风控 ———

@app.get("/api/convertible/terms")
def convertible_terms(ts_code: str, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """可转债条款 LLM 解读（D3 #33）。"""
    from src.data_platform.adapters.tushare_adapter import pull_cb_basic
    from src.astock_analysis.convertible_terms import analyze_convertible_terms
    terms = pull_cb_basic(ts_code)
    if not terms:
        raise HTTPException(404, f"可转债 {ts_code} 条款未找到")
    result = analyze_convertible_terms(terms)
    return {"ts_code": ts_code, "summary": result["summary"], "terms": result["raw_terms"]}


@app.get("/api/risk/state")
def risk_state(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.risk_control import RiskControl
    rc = RiskControl.get()
    return {"halted": rc.is_halted(), "reason": rc.halt_reason(), "rules": rc.get_rules()}


@app.post("/api/risk/halt")
def risk_halt(payload: dict = Depends(require_perm("halt"))):
    from src.risk_control import RiskControl
    RiskControl.get().emergency_halt(f"Web:{payload['username']}")
    audit_log(payload["username"], "emergency_halt", detail="web button")
    return {"halted": True}


@app.post("/api/risk/resume")
def risk_resume(payload: dict = Depends(require_perm("resume"))):
    from src.risk_control import RiskControl
    RiskControl.get().resume()
    audit_log(payload["username"], "risk_resume")
    return {"halted": False}


# ─── 实盘交易开关（第二级分项；第一级 .env ENABLE_LIVE_TRADING 总闸） ───

LIVE_TRADING_MARKETS = ("convertible", "etf", "astock", "binance_perp", "okx_perp")


@app.get("/api/live-trading")
def list_live_trading(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列实盘分项开关 + .env 总闸状态。三级 AND：总闸 AND 分项 AND 策略 enabled。"""
    from src.data_platform.settings import is_live_trading_enabled
    with get_conn() as conn:
        cur = conn.execute("SELECT market, enabled, updated_at FROM live_trading_config ORDER BY market")
        rows = cur.fetchall()
    return {
        "master_enabled": is_live_trading_enabled(),
        "items": [{"market": r[0], "enabled": r[1], "updated_at": r[2]} for r in rows],
    }


@app.put("/api/live-trading/{market}")
def update_live_trading(market: str, enabled: bool = Query(...),
                        payload: dict = Depends(require_perm("live_trading_control"))):
    """开/关某品种实盘分项（trader/admin）。需 .env 总闸也开才真生效。"""
    if market not in LIVE_TRADING_MARKETS:
        raise HTTPException(400, f"未知市场: {market}，可选: {LIVE_TRADING_MARKETS}")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE live_trading_config SET enabled=%s, updated_at=now() WHERE market=%s RETURNING enabled",
            (enabled, market))
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, f"市场不存在: {market}")
    audit_log(payload["username"], "live_trading_toggle", detail=f"{market}={enabled}")
    return {"market": market, "enabled": row[0]}


# ─── LLM 模型配置（DB 化，Admin 管理） ───

class LLMModelReq(BaseModel):
    name: str
    provider: str
    model: str
    api_key: str = ""
    base_url: str
    context_window: int = 32768
    supports_tools: bool = True
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    priority: int = 10
    enabled: bool = False


@app.get("/api/llm-models")
def list_llm_models(payload: dict = Depends(require_perm("llm_config"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, provider, model, api_key_encrypted, base_url, context_window, supports_tools, max_input_tokens, max_output_tokens, temperature, priority, enabled FROM llm_model_config ORDER BY priority")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "provider": r[2], "model": r[3], "has_key": bool(r[4]), "base_url": r[5], "context_window": r[6], "supports_tools": r[7], "max_input_tokens": r[8], "max_output_tokens": r[9], "temperature": r[10], "priority": r[11], "enabled": r[12]} for r in rows]


@app.post("/api/llm-models")
def create_llm_model(req: LLMModelReq, payload: dict = Depends(require_perm("llm_config"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.api_key) if req.api_key else ""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO llm_model_config (name, provider, model, api_key_encrypted, base_url, context_window, supports_tools, max_input_tokens, max_output_tokens, temperature, priority, enabled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (req.name, req.provider, req.model, enc, req.base_url, req.context_window, req.supports_tools, req.max_input_tokens, req.max_output_tokens, req.temperature, req.priority, req.enabled))
        conn.commit()
    audit_log(payload["username"], "llm_model_create", detail=f"{req.provider}/{req.model}")
    return {"id": cur.fetchone()[0]}


@app.put("/api/llm-models/{mid}")
def update_llm_model(mid: int, req: LLMModelReq, payload: dict = Depends(require_perm("llm_config"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.api_key) if req.api_key else None
    with get_conn() as conn:
        if enc is not None:
            conn.execute("UPDATE llm_model_config SET name=%s, provider=%s, model=%s, api_key_encrypted=%s, base_url=%s, context_window=%s, supports_tools=%s, max_input_tokens=%s, max_output_tokens=%s, temperature=%s, priority=%s, enabled=%s, updated_at=now() WHERE id=%s",
                (req.name, req.provider, req.model, enc, req.base_url, req.context_window, req.supports_tools, req.max_input_tokens, req.max_output_tokens, req.temperature, req.priority, req.enabled, mid))
        else:
            conn.execute("UPDATE llm_model_config SET name=%s, provider=%s, model=%s, base_url=%s, context_window=%s, supports_tools=%s, max_input_tokens=%s, max_output_tokens=%s, temperature=%s, priority=%s, enabled=%s, updated_at=now() WHERE id=%s",
                (req.name, req.provider, req.model, req.base_url, req.context_window, req.supports_tools, req.max_input_tokens, req.max_output_tokens, req.temperature, req.priority, req.enabled, mid))
        conn.commit()
    audit_log(payload["username"], "llm_model_update", detail=f"id={mid}")
    from src.llm_gateway.gateway import gateway
    gateway.reload_models()
    return {"id": mid}


@app.delete("/api/llm-models/{mid}")
def delete_llm_model(mid: int, payload: dict = Depends(require_perm("llm_config"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM llm_model_config WHERE id=%s", (mid,))
        conn.commit()
    audit_log(payload["username"], "llm_model_delete", detail=f"id={mid}")
    from src.llm_gateway.gateway import gateway
    gateway.reload_models()
    return {"id": mid}


@app.post("/api/llm-models/{mid}/test")
def test_llm_model(mid: int, payload: dict = Depends(require_perm("llm_config"))):
    from src.web_api.crypto_utils import decrypt
    from openai import OpenAI
    with get_conn() as conn:
        cur = conn.execute("SELECT api_key_encrypted, base_url, model FROM llm_model_config WHERE id=%s", (mid,))
        r = cur.fetchone()
    if not r or not r[0]:
        raise HTTPException(400, "模型未配置 api_key")
    try:
        client = OpenAI(api_key=decrypt(r[0]), base_url=r[1], timeout=15)
        resp = client.chat.completions.create(model=r[2], messages=[{"role": "user", "content": "ping"}], max_tokens=5)
        return {"ok": True, "reply": resp.choices[0].message.content}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── 飞书配置（多机器人 + 扫码接入 + 长连接） ───

@app.get("/api/feishu/list")
def feishu_list(payload: dict = Depends(require_perm("feishu_config"))):
    """列所有飞书机器人（含 role/description）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, app_id, app_secret_encrypted, role, description, enabled, updated_at FROM feishu_config ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "app_id": r[2], "has_secret": bool(r[3]), "role": r[4], "description": r[5], "enabled": r[6], "updated_at": r[7]} for r in rows]


@app.post("/api/feishu/connect")
def feishu_connect(payload: dict = Depends(require_perm("feishu_config"))):
    """扫码创建/连接飞书机器人。"""
    session_id = str(uuid.uuid4())
    from src.feishu_bot.tasks import feishu_register_task
    feishu_register_task.delay(session_id)
    return {"session_id": session_id}


@app.get("/api/feishu/status/{session_id}")
def feishu_status(session_id: str, payload: dict = Depends(require_perm("feishu_config"))):
    r = redis.Redis(connection_pool=_redis_pool_feishu)
    data = r.get(f"feishu:session:{session_id}")
    if not data:
        return {"status": "pending"}
    return json.loads(data)


@app.post("/api/feishu/{fid}/start")
def feishu_start(fid: int, payload: dict = Depends(require_perm("feishu_config"))):
    """启动机器人长连接（systemctl start quant-feishu-bot@<id>，要 polkit）。"""
    try:
        subprocess.run(["systemctl", "start", f"quant-feishu-bot@{fid}"], check=True, timeout=10)
        with get_conn() as conn:
            conn.execute("UPDATE feishu_config SET enabled=true, updated_at=now() WHERE id=%s", (fid,))
            conn.commit()
        audit_log(payload["username"], "feishu_start", detail=f"id={fid}")
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": f"systemctl 失败（polkit?）: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/feishu/{fid}/stop")
def feishu_stop(fid: int, payload: dict = Depends(require_perm("feishu_config"))):
    """停止机器人长连接。"""
    try:
        subprocess.run(["systemctl", "stop", f"quant-feishu-bot@{fid}"], check=True, timeout=10)
        with get_conn() as conn:
            conn.execute("UPDATE feishu_config SET enabled=false, updated_at=now() WHERE id=%s", (fid,))
            conn.commit()
        audit_log(payload["username"], "feishu_stop", detail=f"id={fid}")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class FeishuUpdateReq(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None

@app.put("/api/feishu/{fid}")
def feishu_update(fid: int, req: FeishuUpdateReq, payload: dict = Depends(require_perm("feishu_config"))):
    """改机器人配置（名称/角色/备注）。修改后 role 对后续消息生效。"""
    with get_conn() as conn:
        if req.name is not None:
            conn.execute("UPDATE feishu_config SET name=%s, updated_at=now() WHERE id=%s", (req.name, fid))
        if req.role is not None:
            conn.execute("UPDATE feishu_config SET role=%s, updated_at=now() WHERE id=%s", (req.role, fid))
        if req.description is not None:
            conn.execute("UPDATE feishu_config SET description=%s, updated_at=now() WHERE id=%s", (req.description, fid))
        conn.commit()
    audit_log(payload["username"], "feishu_update", detail=f"id={fid}")
    return {"ok": True}


@app.delete("/api/feishu/{fid}")
def feishu_delete(fid: int, payload: dict = Depends(require_perm("feishu_config"))):
    """删除机器人配置。"""
    try:
        subprocess.run(["systemctl", "stop", f"quant-feishu-bot@{fid}"], check=False, timeout=10)
    except Exception:
        pass
    with get_conn() as conn:
        conn.execute("DELETE FROM feishu_config WHERE id=%s", (fid,))
        conn.commit()
    audit_log(payload["username"], "feishu_delete", detail=f"id={fid}")
    return {"ok": True}


@app.post("/api/feishu/{fid}/test")
def feishu_test(fid: int, payload: dict = Depends(require_perm("feishu_config"))):
    """测机器人连接。"""
    from src.web_api.crypto_utils import decrypt
    with get_conn() as conn:
        cur = conn.execute("SELECT app_id, app_secret_encrypted FROM feishu_config WHERE id=%s", (fid,))
        r = cur.fetchone()
    if not r:
        raise HTTPException(404, "机器人不存在")
    try:
        resp = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": r[0], "app_secret": decrypt(r[1])}, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            return {"ok": True, "tenant_access_token": data.get("tenant_access_token", "")[:10] + "..."}
        return {"ok": False, "error": data.get("msg", str(data))}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- 数据同步 ---

@app.get("/api/sync/config")
def list_sync_config(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, tushare_api, pg_table, data_type, sync_mode, schedule, trade_day_filter, enabled, last_sync_date, last_sync_ts, last_sync_count, last_status, description FROM sync_config ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "tushare_api": r[2], "pg_table": r[3], "data_type": r[4], "sync_mode": r[5], "schedule": r[6], "trade_day_filter": r[7], "enabled": r[8], "last_sync_date": r[9], "last_sync_ts": str(r[10]) if r[10] else None, "last_sync_count": r[11], "last_status": r[12], "description": r[13]} for r in rows]


@app.put("/api/sync/config/{sid}")
def update_sync_config_api(sid: str, body: dict, payload: dict = Depends(require_perm("data_sync"))):
    with get_conn() as conn:
        conn.execute("UPDATE sync_config SET schedule=%s, enabled=%s, trade_day_filter=%s WHERE id=%s",
            (body.get("schedule"), body.get("enabled"), body.get("trade_day_filter"), sid))
        conn.commit()
    audit_log(payload["username"], "update_sync_config", sid)
    return {"ok": True}


@app.post("/api/sync/trigger/{sid}")
def trigger_sync_api(sid: str, backfill_from: str | None = None, payload: dict = Depends(require_perm("data_sync"))):
    """异步触发类型级同步：提交 Celery 后台任务，立即返回 task_id（不阻塞 HTTP）。"""
    from src.scheduler.tasks import sync_via_celery
    task = sync_via_celery.delay(sid, backfill_from)
    audit_log(payload["username"], "trigger_sync", sid)
    return {"status": "submitted", "task_id": task.id}


@app.get("/api/sync/trigger/{sid}/progress")
def trigger_progress_api(sid: str, task_id: str | None = None,
                         payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """查类型级同步进度（Valkey sync:type:{sid}，无则 Celery AsyncResult 兜底）。"""
    r = redis.Redis(connection_pool=_redis_pool)
    data = r.hgetall(f"sync:type:{sid}")
    _INT_FIELDS = {"done", "total", "pct", "rows_pulled", "rows_saved",
                   "expected_days", "actual_days", "failed_dates_count"}
    if data:
        out = {}
        for k, v in data.items():
            ks = k.decode() if isinstance(k, bytes) else k
            vs = v.decode() if isinstance(v, bytes) else v
            out[ks] = int(vs) if ks in _INT_FIELDS and vs.lstrip("-").isdigit() else vs
        return out
    # 兜底：查 Celery AsyncResult（hash 过期/worker 重启时）
    if task_id:
        from src.scheduler.app import app as celery_app
        res = celery_app.AsyncResult(task_id)
        if res.state == "SUCCESS":
            d = res.result or {}
            return {"status": d.get("status", "success"),
                    "rows_pulled": d.get("rows_pulled", 0),
                    "rows_saved": d.get("rows_saved", 0),
                    "expected_days": d.get("expected_days") or 0,
                    "actual_days": d.get("actual_days") or 0,
                    "failed_dates_count": len(d.get("failed_dates") or [])}
        if res.state in ("PENDING", "STARTED", "RETRY"):
            return {"status": "running"}
        if res.state == "FAILURE":
            return {"status": "error", "error": str(res.result)[:120]}
    return {"status": "idle"}


# --- per-symbol 同步端点（2026-08-04 端点误删恢复，基于 engine 现有函数重建） ---

@app.get("/api/sync/symbols/{sid}")
def list_symbols_api(sid: str, q: str = "", page: int = 1, size: int = 9999,
                     payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.data_sync.engine import list_symbols
    return list_symbols(sid, q=q, page=page, size=size)


@app.post("/api/sync/symbol/{sid}/{ts_code}")
def sync_symbol_api(sid: str, ts_code: str, body: dict = Body(default={}),
                    payload: dict = Depends(require_perm("data_sync"))):
    from src.data_sync.engine import sync_symbol
    mode = body.get("mode", "auto") if body else "auto"
    result = sync_symbol(sid, ts_code, mode=mode)
    audit_log(payload["username"], "sync_symbol", f"{sid}:{ts_code}")
    return result


@app.post("/api/sync/symbol/{sid}/{ts_code}/backfill")
def backfill_symbol_api(sid: str, ts_code: str, body: dict = Body(...),
                        payload: dict = Depends(require_perm("data_sync"))):
    from src.data_sync.engine import backfill_symbol
    result = backfill_symbol(sid, ts_code, body.get("start", ""), body.get("end", ""))
    audit_log(payload["username"], "backfill_symbol", f"{sid}:{ts_code}:{body.get('start')}~{body.get('end')}")
    return result


@app.delete("/api/sync/symbol/{sid}/{ts_code}")
def delete_symbol_api(sid: str, ts_code: str,
                      payload: dict = Depends(require_perm("data_sync"))):
    from src.data_sync.engine import delete_symbol
    result = delete_symbol(sid, ts_code)
    audit_log(payload["username"], "delete_symbol", f"{sid}:{ts_code}")
    return result


@app.post("/api/sync/all/{sid}")
def sync_all_api(sid: str, payload: dict = Depends(require_perm("data_sync"))):
    """提交全市场全量重建（Celery 后台，返回 task_id）。"""
    from src.scheduler.tasks import sync_all_symbols
    task = sync_all_symbols.delay(sid)
    audit_log(payload["username"], "sync_all", sid)
    return {"task_id": task.id}


@app.get("/api/sync/all/{sid}/progress")
def sync_all_progress_api(sid: str, task_id: str | None = None,
                           payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """查全量重建进度（Valkey sync:progress:{sid}，无则 Celery AsyncResult 兜底）。"""
    r = redis.Redis(connection_pool=_redis_pool)
    data = r.hgetall(f"sync:progress:{sid}")
    _INT_FIELDS = {"done", "total", "pct", "ok", "saved", "failed_count"}
    if data:
        out = {}
        for k, v in data.items():
            ks = k.decode() if isinstance(k, bytes) else k
            vs = v.decode() if isinstance(v, bytes) else v
            out[ks] = int(vs) if ks in _INT_FIELDS and vs.lstrip("-").isdigit() else vs
        return out
    if task_id:
        from src.scheduler.app import app as celery_app
        res = celery_app.AsyncResult(task_id)
        if res.state == "SUCCESS":
            d = res.result or {}
            return {"status": d.get("status", "success"),
                    "ok": d.get("ok", 0), "total": d.get("total", 0),
                    "saved": d.get("saved", 0), "failed_count": d.get("failed_count", 0)}
        if res.state in ("PENDING", "STARTED", "RETRY"):
            return {"status": "running"}
        if res.state == "FAILURE":
            return {"status": "error", "error": str(res.result)[:120]}
    return {"status": "idle"}


@app.delete("/api/sync/data/{sid}")
def delete_sync_data_api(sid: str, payload: dict = Depends(require_perm("data_sync"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT pg_table FROM sync_config WHERE id=%s", (sid,))
        r = cur.fetchone()
    if r and r[0]:
        with get_conn() as conn:
            conn.execute(f'DELETE FROM "{r[0]}"')
            conn.execute("UPDATE sync_config SET last_sync_date=NULL, last_sync_ts=NULL, last_sync_count=0, last_status='idle' WHERE id=%s", (sid,))
            conn.commit()
    audit_log(payload["username"], "delete_sync_data", sid)
    return {"ok": True}


@app.get("/api/sync/log")
def get_sync_logs_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, sync_id, mode, start, end, pulled, saved, status, ts FROM sync_log ORDER BY ts DESC LIMIT 100")
        rows = cur.fetchall()
    return [{"id": r[0], "sync_id": r[1], "mode": r[2], "start": r[3], "end": r[4], "pulled": r[5], "saved": r[6], "status": r[7], "ts": str(r[8]) if r[8] else None} for r in rows]


@app.get("/api/kline/{symbol}")
def get_kline_api(symbol: str, days: int = 0,
                  payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """K线数据（days=0 全历史，>0 按日历日截断；2026-08-04 端点误删恢复）。

    symbol 接受 ts_code（600000.SH）或 vt_symbol（600000.SHSE），内部 to_vt_symbol 转换查 bar_1D。
    返回 [{ts, open, high, low, close, volume}, ...]。
    """
    from src.data_platform.db import get_bars
    from src.data_platform.schema import to_vt_symbol
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=days) if days > 0 else date(2010, 1, 1)
    vt = to_vt_symbol(symbol)
    df = get_bars(vt, "1D", start, end)
    if df is None or df.empty:
        return []
    records = df[["ts", "open", "high", "low", "close", "volume"]].to_dict("records")
    for r in records:
        r["ts"] = r["ts"].strftime("%Y-%m-%d") if pd.notna(r["ts"]) else None
        for k in ("open", "high", "low", "close", "volume"):
            r[k] = float(r[k]) if pd.notna(r[k]) else None
    return records


# --- 三市场筛选端点（2026-08-04 端点误删恢复） ---

@app.get("/api/screen/astock")
def screen_astock_api(pe_max: float = 0, pb_max: float = 0, mv_min: float = 0,
                      turnover_min: float = 0, limit: int = 100,
                      payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """A股基本面筛选（daily_basic 最新交易日 + join asset_static_info name）。"""
    _f = lambda x: float(x) if x is not None else None
    with get_conn() as conn:
        cur = conn.execute("""
            SELECT d.ts_code, s.name, d.close, d.pe, d.pe_ttm, d.pb, d.turnover_rate, d.total_mv
            FROM daily_basic d
            LEFT JOIN asset_static_info s ON s.ts_code = d.ts_code
            WHERE d.trade_date = (SELECT max(trade_date) FROM daily_basic)
              AND (%s = 0 OR d.pe <= %s)
              AND (%s = 0 OR d.pb <= %s)
              AND (%s = 0 OR d.total_mv >= %s)
              AND (%s = 0 OR d.turnover_rate >= %s)
            ORDER BY d.total_mv DESC NULLS LAST
            LIMIT %s
        """, (pe_max, pe_max, pb_max, pb_max, mv_min, mv_min, turnover_min, turnover_min, limit))
        rows = cur.fetchall()
    return [{"ts_code": r[0], "name": r[1], "close": _f(r[2]), "pe": _f(r[3]),
             "pe_ttm": _f(r[4]), "pb": _f(r[5]), "turnover": _f(r[6]), "total_mv": _f(r[7])}
            for r in rows]


@app.get("/api/screen/cb")
def screen_cb_api(limit: int = 100,
                  payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """可转债筛选（cb_basic_info）。"""
    _f = lambda x: float(x) if x is not None else None
    with get_conn() as conn:
        cur = conn.execute("""
            SELECT ts_code, bond_short_name, stk_code, stk_short_name, conv_price, maturity_date
            FROM cb_basic_info ORDER BY ts_code LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    return [{"ts_code": r[0], "name": r[1], "stk_code": r[2], "stk_name": r[3],
             "conv_price": _f(r[4]), "maturity_date": str(r[5]) if r[5] else ""} for r in rows]


@app.get("/api/screen/etf")
def screen_etf_api(limit: int = 100,
                   payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """ETF 基金筛选（etf_basic_info）。"""
    with get_conn() as conn:
        cur = conn.execute("""
            SELECT ts_code, name, management, fund_type
            FROM etf_basic_info ORDER BY ts_code LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    return [{"ts_code": r[0], "name": r[1], "management": r[2], "fund_type": r[3]} for r in rows]


# --- LLM 用量监控看板（PT2，llm_usage 表已就绪 migration 0011）---

@app.get("/api/llm-usage/summary")
def llm_usage_summary(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """LLM 用量汇总：今日/本月（按 provider/model）+ 近 7 天趋势。"""
    with get_conn() as conn:
        cur = conn.execute("""
            SELECT provider, model, count(*),
                   COALESCE(sum(input_tokens),0), COALESCE(sum(output_tokens),0),
                   COALESCE(avg(latency_ms),0),
                   CASE WHEN count(*)>0 THEN round(sum(CASE WHEN success THEN 1 ELSE 0 END)*100.0/count(*),1) ELSE 0 END
            FROM llm_usage WHERE ts::date = current_date
            GROUP BY provider, model ORDER BY count(*) DESC
        """)
        today = [{"provider": r[0], "model": r[1], "calls": r[2], "input_tokens": int(r[3]),
                  "output_tokens": int(r[4]), "avg_latency_ms": int(r[5]), "success_rate": float(r[6])}
                 for r in cur.fetchall()]
        cur = conn.execute("""
            SELECT provider, model, count(*), COALESCE(sum(input_tokens),0), COALESCE(sum(output_tokens),0),
                   COALESCE(avg(latency_ms),0),
                   CASE WHEN count(*)>0 THEN round(sum(CASE WHEN success THEN 1 ELSE 0 END)*100.0/count(*),1) ELSE 0 END
            FROM llm_usage WHERE date_trunc('month', ts) = date_trunc('month', current_date)
            GROUP BY provider, model ORDER BY count(*) DESC
        """)
        month = [{"provider": r[0], "model": r[1], "calls": r[2], "input_tokens": int(r[3]),
                  "output_tokens": int(r[4]), "avg_latency_ms": int(r[5]), "success_rate": float(r[6])}
                 for r in cur.fetchall()]
        cur = conn.execute("""
            SELECT ts::date AS d, count(*), COALESCE(sum(input_tokens+output_tokens),0), COALESCE(avg(latency_ms),0)
            FROM llm_usage WHERE ts >= current_date - interval '7 days'
            GROUP BY d ORDER BY d
        """)
        trend = [{"date": str(r[0]), "calls": r[1], "total_tokens": int(r[2]), "avg_latency_ms": int(r[3])}
                 for r in cur.fetchall()]
    return {"today": today, "month": month, "trend": trend}


# --- 数据源管理（PT3 平台化数据层） ---

class LlmBudgetReq(BaseModel):
    provider: str | None = None
    daily_token_limit: int | None = None
    monthly_cost_limit: float | None = None
    alert_threshold_pct: int = 80
    enabled: bool = True


def check_budget_alerts() -> dict:
    """检查所有 enabled budget，超阈值发告警。返回 {checked, alerts}（D5 #38）。"""
    from src.alert_notify.channel import get_channel
    alerts = []
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, provider, daily_token_limit, monthly_cost_limit, alert_threshold_pct, enabled "
            "FROM llm_budget WHERE enabled=true"
        )
        budgets = cur.fetchall()
    for b in budgets:
        b_id, b_provider, b_daily_limit, b_monthly_cost, b_threshold, b_enabled = b
        if not b_daily_limit:
            continue
        sql = "SELECT COALESCE(sum(input_tokens+output_tokens),0) FROM llm_usage WHERE ts::date=current_date"
        params = []
        if b_provider:
            sql += " AND provider=%s"
            params.append(b_provider)
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            today = cur.fetchone()[0] or 0
        limit = b_daily_limit * b_threshold // 100
        if today > limit:
            provider_name = b_provider or "全局"
            try:
                ch = get_channel("wechat_work")
                ch.send(
                    title="LLM 预算预警",
                    body=f"{provider_name} 今日 {today} token 超阈值 {limit}（{b_threshold}%）",
                    level="warn",
                )
                sent = True
            except Exception:
                sent = False
            alerts.append({"provider": provider_name, "today_tokens": today, "limit": b_daily_limit,
                           "threshold": limit, "sent": sent})
    return {"checked": len(budgets), "alerts": alerts}


@app.get("/api/llm-budget")
def list_llm_budget(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列出预算配置（D5 #38）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, provider, daily_token_limit, monthly_cost_limit, "
            "alert_threshold_pct, enabled, updated_at FROM llm_budget ORDER BY id"
        )
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "daily_token_limit": r[2],
             "monthly_cost_limit": float(r[3]) if r[3] else None,
             "alert_threshold_pct": r[4], "enabled": r[5],
             "updated_at": str(r[6]) if r[6] else None} for r in rows]


@app.put("/api/llm-budget/{bid}")
def update_llm_budget(bid: int, req: LlmBudgetReq,
                      payload: dict = Depends(require_role("admin"))):
    """更新预算配置（P3-13 权限修正：admin only）。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE llm_budget SET provider=%s, daily_token_limit=%s, monthly_cost_limit=%s, "
            "alert_threshold_pct=%s, enabled=%s, updated_at=now() WHERE id=%s",
            (req.provider, req.daily_token_limit, req.monthly_cost_limit,
             req.alert_threshold_pct, req.enabled, bid),
        )
        conn.commit()
    audit_log(payload["username"], "update_llm_budget", str(bid))
    return {"ok": True}


@app.post("/api/llm-budget/check")
def check_budget(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """手动触发预算告警检查。"""
    result = check_budget_alerts()
    return result


class DataSourceReq(BaseModel):
    provider: str
    name: str
    credentials: str = ""
    params: str | None = None
    usage_limit: int | None = None
    enabled: bool = True


@app.get("/api/data-sources")
def list_data_sources(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, provider, name, credentials_encrypted IS NOT NULL, params, usage_limit, enabled, updated_at FROM data_source_config ORDER BY provider")
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "has_credentials": bool(r[3]),
             "params": r[4], "usage_limit": r[5], "enabled": r[6],
             "updated_at": str(r[7]) if r[7] else None} for r in rows]


@app.post("/api/data-sources")
def create_data_source(req: DataSourceReq, payload: dict = Depends(require_role("admin"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO data_source_config (provider, name, credentials_encrypted, params, usage_limit, enabled) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (req.provider, req.name, enc, req.params, req.usage_limit, req.enabled))
        conn.commit()
    audit_log(payload["username"], "data_source_create", req.provider)
    return {"id": cur.fetchone()[0]}


@app.put("/api/data-sources/{dsid}")
def update_data_source(dsid: int, req: DataSourceReq, payload: dict = Depends(require_role("admin"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        if enc is not None:
            conn.execute("UPDATE data_source_config SET provider=%s, name=%s, credentials_encrypted=%s, params=%s, usage_limit=%s, enabled=%s, updated_at=now() WHERE id=%s",
                         (req.provider, req.name, enc, req.params, req.usage_limit, req.enabled, dsid))
        else:
            conn.execute("UPDATE data_source_config SET provider=%s, name=%s, params=%s, usage_limit=%s, enabled=%s, updated_at=now() WHERE id=%s",
                         (req.provider, req.name, req.params, req.usage_limit, req.enabled, dsid))
        conn.commit()
    audit_log(payload["username"], "data_source_update", f"id={dsid}")
    return {"ok": True}


@app.delete("/api/data-sources/{dsid}")
def delete_data_source(dsid: int, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM data_source_config WHERE id=%s", (dsid,))
        conn.commit()
    audit_log(payload["username"], "data_source_delete", f"id={dsid}")
    return {"ok": True}


@app.post("/api/data-sources/{dsid}/test")
def test_data_source(dsid: int, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.data_platform.data_source import _REGISTRY
    with get_conn() as conn:
        cur = conn.execute("SELECT provider, credentials_encrypted, params FROM data_source_config WHERE id=%s", (dsid,))
        r = cur.fetchone()
    if not r:
        return {"ok": False, "error": "数据源不存在"}
    cls = _REGISTRY.get(r[0])
    if not cls:
        return {"ok": False, "error": f"provider {r[0]} 未注册（需实现 DataSource 子类）"}
    ds = cls(credentials_encrypted=r[1], params=r[2])
    ok = ds.test_connection()
    return {"ok": ok, "error": "" if ok else "连接测试失败，看日志"}


# --- 后台任务管理（PT1 平台化核心） ---

@app.get("/api/tasks")
def list_tasks_api(status: str | None = None, limit: int = 100,
                   payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.task_manager import list_tasks
    return {"items": list_tasks(status=status, limit=limit)}


@app.get("/api/tasks/{task_id}")
def get_task_api(task_id: str,
                 payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.task_manager import get_task
    t = get_task(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


@app.post("/api/tasks/{task_id}/terminate")
def terminate_task_api(task_id: str,
                       payload: dict = Depends(require_role("trader", "admin"))):
    from src.task_manager import terminate_task, log_task
    terminate_task(task_id)
    log_task(task_id, "WARN", f"用户 {payload['username']} 终止任务")
    audit_log(payload["username"], "task_terminate", task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/force-delete")
def force_delete_task_api(task_id: str,
                          payload: dict = Depends(require_role("admin"))):
    from src.task_manager import force_delete_task
    force_delete_task(task_id)
    audit_log(payload["username"], "task_force_delete", task_id)
    return {"ok": True}


@app.post("/api/tasks/detect-stuck")
def detect_stuck_api(payload: dict = Depends(require_role("admin"))):
    from src.task_manager import detect_stuck
    count = detect_stuck()
    return {"stuck_count": count}


# --- 消息通道管理（PT4 平台化消息层） ---

class ChannelReq(BaseModel):
    provider: str
    name: str
    credentials: str = ""
    params: str | None = None
    enabled: bool = True


@app.get("/api/channels")
def list_channels(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, provider, name, credentials_encrypted IS NOT NULL, params, enabled, updated_at FROM channel_config ORDER BY provider")
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "has_credentials": bool(r[3]),
             "params": r[4], "enabled": r[5], "updated_at": str(r[6]) if r[6] else None} for r in rows]


@app.post("/api/channels")
def create_channel(req: ChannelReq, payload: dict = Depends(require_role("admin"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO channel_config (provider, name, credentials_encrypted, params, enabled) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (req.provider, req.name, enc, req.params, req.enabled))
        conn.commit()
    audit_log(payload["username"], "channel_create", req.provider)
    return {"id": cur.fetchone()[0]}


@app.put("/api/channels/{cid}")
def update_channel(cid: int, req: ChannelReq, payload: dict = Depends(require_role("admin"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        if enc is not None:
            conn.execute("UPDATE channel_config SET provider=%s, name=%s, credentials_encrypted=%s, params=%s, enabled=%s, updated_at=now() WHERE id=%s",
                         (req.provider, req.name, enc, req.params, req.enabled, cid))
        else:
            conn.execute("UPDATE channel_config SET provider=%s, name=%s, params=%s, enabled=%s, updated_at=now() WHERE id=%s",
                         (req.provider, req.name, req.params, req.enabled, cid))
        conn.commit()
    audit_log(payload["username"], "channel_update", f"id={cid}")
    return {"ok": True}


@app.delete("/api/channels/{cid}")
def delete_channel(cid: int, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM channel_config WHERE id=%s", (cid,))
        conn.commit()
    audit_log(payload["username"], "channel_delete", f"id={cid}")
    return {"ok": True}


@app.post("/api/channels/{cid}/test")
def test_channel(cid: int, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.alert_notify.channel import _REGISTRY
    from src.web_api.crypto_utils import decrypt
    with get_conn() as conn:
        cur = conn.execute("SELECT provider, credentials_encrypted FROM channel_config WHERE id=%s", (cid,))
        r = cur.fetchone()
    if not r:
        return {"ok": False, "error": "通道不存在"}
    cls = _REGISTRY.get(r[0])
    if not cls:
        return {"ok": False, "error": f"provider {r[0]} 未注册（需实现 MessageChannel 子类）"}
    cred = decrypt(r[1]) if r[1] else ""
    ch = cls(cred)
    ok = ch.test()
    return {"ok": ok, "error": "" if ok else "发送失败，看日志"}


# --- 交易通道管理（PT5 平台化交易层） ---

class BrokerReq(BaseModel):
    provider: str
    name: str
    credentials: str = ""           # JSON 字符串（如 {"app_id":"...","app_secret":"..."}）
    params: str | None = None
    enabled: bool = True


@app.get("/api/brokers")
def list_brokers(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, provider, name, credentials_encrypted IS NOT NULL, params, enabled, updated_at FROM broker_config ORDER BY provider")
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "has_credentials": bool(r[3]),
             "params": r[4], "enabled": r[5], "updated_at": str(r[6]) if r[6] else None} for r in rows]


@app.post("/api/brokers")
def create_broker(req: BrokerReq, payload: dict = Depends(require_role("admin"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO broker_config (provider, name, credentials_encrypted, params, enabled) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (req.provider, req.name, enc, req.params, req.enabled))
        conn.commit()
    audit_log(payload["username"], "broker_create", req.provider)
    return {"id": cur.fetchone()[0]}


@app.put("/api/brokers/{bid}")
def update_broker(bid: int, req: BrokerReq, payload: dict = Depends(require_role("admin"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        if enc is not None:
            conn.execute("UPDATE broker_config SET provider=%s, name=%s, credentials_encrypted=%s, params=%s, enabled=%s, updated_at=now() WHERE id=%s",
                         (req.provider, req.name, enc, req.params, req.enabled, bid))
        else:
            conn.execute("UPDATE broker_config SET provider=%s, name=%s, params=%s, enabled=%s, updated_at=now() WHERE id=%s",
                         (req.provider, req.name, req.params, req.enabled, bid))
        conn.commit()
    audit_log(payload["username"], "broker_update", f"id={bid}")
    return {"ok": True}


@app.delete("/api/brokers/{bid}")
def delete_broker(bid: int, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM broker_config WHERE id=%s", (bid,))
        conn.commit()
    audit_log(payload["username"], "broker_delete", f"id={bid}")
    return {"ok": True}


@app.post("/api/brokers/{bid}/test")
def test_broker(bid: int, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.strategy_framework.broker import _REGISTRY
    with get_conn() as conn:
        cur = conn.execute("SELECT provider, credentials_encrypted, params FROM broker_config WHERE id=%s", (bid,))
        r = cur.fetchone()
    if not r:
        return {"ok": False, "error": "通道不存在"}
    cls = _REGISTRY.get(r[0])
    if not cls:
        return {"ok": False, "error": f"provider {r[0]} 未注册（需实现 Broker 子类）"}
    b = cls(credentials_encrypted=r[1], params=r[2])
    ok = b.test_connection()
    return {"ok": ok, "error": "" if ok else "凭证不完整或连接失败（真连 vnpy 在服务器）"}


# --- 风控规则管理（PT6 平台化风控） ---

class RiskRuleReq(BaseModel):
    name: str
    type: str
    params: str = "{}"               # JSON: {max_pct:0.1} 等
    enabled: bool = True


@app.get("/api/risk-rules")
def list_risk_rules(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, type, params, enabled, updated_at FROM risk_rules ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "params": r[3],
             "enabled": r[4], "updated_at": str(r[5]) if r[5] else None} for r in rows]


@app.get("/api/risk-rules/types")
def list_risk_rule_types(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列已注册的规则类型（前端下拉）"""
    from src.risk_control.risk_rule import _REGISTRY
    return {"types": list(_REGISTRY.keys())}


@app.post("/api/risk-rules")
def create_risk_rule(req: RiskRuleReq, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO risk_rules (name, type, params, enabled) VALUES (%s,%s,%s,%s) RETURNING id",
            (req.name, req.type, req.params, req.enabled))
        conn.commit()
    audit_log(payload["username"], "risk_rule_create", req.type)
    return {"id": cur.fetchone()[0]}


@app.put("/api/risk-rules/{rid}")
def update_risk_rule(rid: int, req: RiskRuleReq, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("UPDATE risk_rules SET name=%s, type=%s, params=%s, enabled=%s, updated_at=now() WHERE id=%s",
                     (req.name, req.type, req.params, req.enabled, rid))
        conn.commit()
    audit_log(payload["username"], "risk_rule_update", f"id={rid}")
    return {"ok": True}


@app.delete("/api/risk-rules/{rid}")
def delete_risk_rule(rid: int, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM risk_rules WHERE id=%s", (rid,))
        conn.commit()
    audit_log(payload["username"], "risk_rule_delete", f"id={rid}")
    return {"ok": True}


# --- 因子 + 三账对账（#2 + #7） ---

@app.get("/api/factors")
def list_factors_api(category: str | None = None, static_only: bool = False,
                     payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.strategy_framework.factor import list_factors
    return {"items": list_factors(category, static_only=static_only)}


@app.post("/api/factors")
def create_factor_api(req: dict = Body(...),
                       payload: dict = Depends(require_perm("strategy_control"))):
    """创建自定义因子（因子平台化）。"""
    from src.strategy_framework.factor import register_custom_factor
    try:
        result = register_custom_factor(
            name=req.get("name", ""),
            category=req.get("category", "custom"),
            code=req.get("code", ""),
            description=req.get("description", ""),
            params=req.get("params", {}),
            needs_history=int(req.get("needs_history", 0)),
        )
        audit_log(payload["username"], "create_factor", req.get("name", ""))
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/factors/{name}")
def update_factor_api(name: str, req: dict = Body(...),
                       payload: dict = Depends(require_perm("strategy_control"))):
    """更新自定义因子。"""
    from src.strategy_framework.factor import register_custom_factor
    try:
        result = register_custom_factor(
            name=name,
            category=req.get("category", "custom"),
            code=req.get("code", ""),
            description=req.get("description", ""),
            params=req.get("params", {}),
            needs_history=int(req.get("needs_history", 0)),
        )
        audit_log(payload["username"], "update_factor", name)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/factors/{name}")
def delete_factor_api(name: str,
                       payload: dict = Depends(require_perm("strategy_control"))):
    """删除自定义因子。"""
    from src.strategy_framework.factor import delete_custom_factor
    ok = delete_custom_factor(name)
    if not ok:
        raise HTTPException(404, f"因子 {name} 不存在或非自定义因子")
    audit_log(payload["username"], "delete_factor", name)
    return {"ok": True}


@app.post("/api/factors/validate")
def validate_factor_code_api(code: dict = Body(...),
                              payload: dict = Depends(require_role("analyst", "trader", "admin"))):
    """校验因子 Python 代码。"""
    from src.strategy_framework.factor import _check_ast_blacklist, _make_factor_class
    code_str = code.get("code", "")
    name = code.get("name", "test")
    # AST 校验
    forbidden = _check_ast_blacklist(code_str)
    if forbidden:
        return {"valid": False, "error": forbidden}
    # 编译校验
    try:
        _make_factor_class(name, code_str, {})
        return {"valid": True}
    except ValueError as e:
        return {"valid": False, "error": str(e)}


@app.get("/api/reconcile")
def reconcile_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """三账对账（signal_log/order_log/trade_log 比对，同步执行）。"""
    from src.scheduler.tasks import reconcile_three_books
    return reconcile_three_books.apply().get()


# --- 审计日志 ---

@app.get("/api/audit")
def get_audit(payload: dict = Depends(require_perm("user_mgmt"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, ts, actor, action, detail FROM audit_log ORDER BY ts DESC LIMIT 100")
        rows = cur.fetchall()
    return [{"id": r[0], "ts": str(r[1]) if r[1] else None, "actor": r[2], "action": r[3], "detail": r[4]} for r in rows]


# --- 数据完整性看板（A3 #9）---

@app.get("/api/data-integrity")
def data_integrity_api(freq: str = "1D",
                      payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """数据完整性看板：每标的本地条数 vs 预期，算完整性%。

    freq: 1D（按 trade_cal 交易日）/ 1min / 5min（按自然日 × bars_per_day）。
    返回 {items:[{symbol, local_count, first, last, expected, pct, status}], summary}
    status: complete(>=99%) / partial(>0%) / missing(0)
    """
    if freq not in ("1D", "1min", "5min"):
        return {"error": "freq 必须是 1D/1min/5min"}
    table = "bar_1D" if freq == "1D" else f"bar_{freq}"
    bars_per_day = {"1D": 1, "1min": 240, "5min": 48}[freq]
    with get_conn() as conn:
        try:
            cur = conn.execute(
                f"SELECT symbol, count(*), min(ts)::date, max(ts)::date FROM {table} GROUP BY symbol ORDER BY symbol")
            rows = cur.fetchall()
        except Exception:
            return {"items": [], "summary": {"total": 0, "complete": 0, "partial": 0, "missing": 0}}
        if freq == "1D":
            cur = conn.execute("SELECT cal_date FROM trade_cal WHERE is_open=1")
            day_set = {r[0] for r in cur.fetchall()}
        else:
            day_set = None

    items = []
    complete = partial = missing = 0
    for sym, cnt, first, last in rows:
        if not first or not last or cnt == 0:
            missing += 1
            items.append({"symbol": sym, "local_count": cnt, "first": None, "last": None,
                          "expected": 0, "pct": 0, "status": "missing"})
            continue
        if freq == "1D":
            # trade_cal 可能不全（只近年同步），fallback 工作日估算取大值
            tc_count = sum(1 for d in day_set if first <= d <= last) if day_set else 0
            workday_est = (last - first).days * 5 // 7  # 每周 5 工作日粗估
            expected = max(tc_count, workday_est)
        else:
            expected = ((last - first).days + 1) * bars_per_day
        pct = round(cnt / expected * 100, 1) if expected else 0
        status = "complete" if pct >= 99 else ("partial" if pct > 0 else "missing")
        if status == "complete": complete += 1
        elif status == "partial": partial += 1
        else: missing += 1
        items.append({"symbol": sym, "local_count": cnt, "first": str(first), "last": str(last),
                      "expected": expected, "pct": pct, "status": status})
    return {"items": items, "summary": {"total": len(items), "complete": complete,
                                        "partial": partial, "missing": missing}}


# --- 数据源用量监控（A4 #36）---

@app.get("/api/data-source-usage")
def data_source_usage_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """数据源调用量监控：by provider 今日聚合 + 7 天趋势。"""
    with get_conn() as conn:
        try:
            cur = conn.execute("""
                SELECT provider,
                    coalesce(sum(calls), 0) as calls,
                    count(*) as records,
                    sum(case when success then 0 else 1 end) as failures,
                    coalesce(round(avg(latency_ms)), 0) as avg_latency
                FROM data_source_usage
                WHERE ts >= date_trunc('day', now())
                GROUP BY provider ORDER BY calls DESC NULLS LAST
            """)
            today = [{"provider": r[0], "calls": r[1], "records": r[2],
                      "failures": r[3], "avg_latency": r[4]} for r in cur.fetchall()]
            cur = conn.execute("""
                SELECT to_char(date_trunc('day', ts), 'YYYYMMDD') as day,
                       provider, coalesce(sum(calls), 0)
                FROM data_source_usage
                WHERE ts >= now() - interval '7 days'
                GROUP BY day, provider ORDER BY day, provider
            """)
            trend = [{"day": r[0], "provider": r[1], "calls": r[2]} for r in cur.fetchall()]
        except Exception:
            return {"today": [], "trend": []}
    return {"today": today, "trend": trend}


# --- 回测组（B3 #1）---

class PoolReq(BaseModel):
    id: str
    name: str
    category: str = "astock"
    symbolsStr: str = ""
    description: str = ""


@app.get("/api/pool")
def list_pools(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """标的池列表（含 symbols，#22）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM pools LIMIT 1")
        except Exception:
            logger.warning("list_pools: pools 表不存在（需运行 alembic upgrade head）")
        try:
            conn.execute("SELECT 1 FROM pool_symbols LIMIT 1")
        except Exception:
            logger.warning("list_pools: pool_symbols 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute(
            "SELECT p.id, p.name, p.category, p.description, ps.symbol "
            "FROM pools p LEFT JOIN pool_symbols ps ON ps.pool_id=p.id ORDER BY p.id")
        rows = cur.fetchall()
    pools = {}
    for pid, pname, pcat, pdesc, sym in rows:
        if pid not in pools:
            pools[pid] = {"id": pid, "name": pname, "category": pcat, "description": pdesc, "symbols": []}
        if sym:
            pools[pid]["symbols"].append(sym)
    return list(pools.values())


@app.post("/api/pool")
def create_pool(req: PoolReq, payload: dict = Depends(require_perm("strategy_control"))):
    """新建/更新标的池（#22）。"""
    symbols = [s.strip() for s in (req.symbolsStr or "").split("\n") if s.strip()]
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM pools LIMIT 1")
        except Exception:
            logger.warning("create_pool: pools 表不存在（需运行 alembic upgrade head）")
        try:
            conn.execute("SELECT 1 FROM pool_symbols LIMIT 1")
        except Exception:
            logger.warning("create_pool: pool_symbols 表不存在（需运行 alembic upgrade head）")
        conn.execute(
            "INSERT INTO pools (id, name, category, description) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, category=EXCLUDED.category, description=EXCLUDED.description",
            (req.id, req.name, req.category, req.description))
        conn.execute("DELETE FROM pool_symbols WHERE pool_id=%s", (req.id,))
        for sym in symbols:
            conn.execute("INSERT INTO pool_symbols (pool_id, symbol) VALUES (%s,%s) ON CONFLICT DO NOTHING", (req.id, sym))
        conn.commit()
    audit_log(payload["username"], "create_pool", req.id)
    return {"ok": True, "id": req.id, "count": len(symbols)}


class StrategyAccountReq(BaseModel):
    strategy_id: str
    account_id: str
    broker_provider: str = "xtp"
    initial_capital: float = 1000000
    leverage: int = 1


@app.get("/api/strategy_account")
def list_strategy_account(strategy_id: str | None = None, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """策略-账户绑定列表（可按 strategy_id 过滤，#27）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM strategy_account LIMIT 1")
        except Exception:
            logger.warning("list_strategy_account: strategy_account 表不存在（需运行 alembic upgrade head）")
        if strategy_id:
            cur = conn.execute(
                "SELECT id, strategy_id, account_id, broker_provider, initial_capital, leverage, created_at "
                "FROM strategy_account WHERE strategy_id=%s ORDER BY id", (strategy_id,))
        else:
            cur = conn.execute(
                "SELECT id, strategy_id, account_id, broker_provider, initial_capital, leverage, created_at "
                "FROM strategy_account ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "strategy_id": r[1], "account_id": r[2], "broker_provider": r[3],
             "initial_capital": float(r[4]) if r[4] else 0, "leverage": r[5],
             "created_at": str(r[6]) if r[6] else None} for r in rows]


@app.post("/api/strategy_account")
def bind_strategy_account(req: StrategyAccountReq, payload: dict = Depends(require_perm("strategy_control"))):
    """绑定策略-账户（#27）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM strategy_account LIMIT 1")
        except Exception:
            logger.warning("bind_strategy_account: strategy_account 表不存在（需运行 alembic upgrade head）")
        conn.execute(
            "INSERT INTO strategy_account (strategy_id, account_id, broker_provider, initial_capital, leverage) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (strategy_id, account_id) DO UPDATE SET "
            "broker_provider=EXCLUDED.broker_provider, initial_capital=EXCLUDED.initial_capital, leverage=EXCLUDED.leverage",
            (req.strategy_id, req.account_id, req.broker_provider, req.initial_capital, req.leverage))
        conn.commit()
    audit_log(payload["username"], "bind_strategy_account", req.strategy_id)
    return {"ok": True}


@app.delete("/api/strategy_account/{said}")
def unbind_strategy_account(said: int, payload: dict = Depends(require_perm("strategy_control"))):
    """解绑策略-账户（#27）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM strategy_account WHERE id=%s", (said,))
        conn.commit()
    return {"ok": True}


@app.delete("/api/pool/{pid}")
def delete_pool(pid: str, payload: dict = Depends(require_perm("strategy_control"))):
    """删除标的池（CASCADE 删 symbols，#22）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM pools WHERE id=%s", (pid,))
        conn.commit()
    return {"ok": True}


@app.post("/api/backtest")
def create_backtest_api(body: dict = Body(...),
                        payload: dict = Depends(require_perm("strategy_control"))):
    """启动回测 run：写 backtest_runs + Celery backtest_run_task。

    支持 symbol_params：per-symbol 参数覆盖。
    """
    strategy_id = body.get("strategy_config_id")
    symbols = body.get("symbols", [])
    pool_id = body.get("pool_id")
    if pool_id:
        with get_conn() as conn:
            cur = conn.execute("SELECT symbol FROM pool_symbols WHERE pool_id=%s", (pool_id,))
            symbols = [r[0] for r in cur.fetchall()]
    if not symbols or not strategy_id:
        raise HTTPException(400, "需 strategy_config_id + symbols/pool_id")
    params = body.get("params", {})
    symbol_params = body.get("symbol_params", {})  # per-symbol 参数覆盖
    mode = body.get("mode", "single")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO backtest_runs (strategy_config_id, symbols, params, symbol_params, mode, status) "
            "VALUES (%s,%s,%s,%s,%s,'pending') RETURNING id",
            (strategy_id, json.dumps(symbols), json.dumps(params),
             json.dumps(symbol_params), mode))
        run_id = cur.fetchone()[0]
        conn.commit()
    from src.scheduler.tasks import backtest_run_task
    task = backtest_run_task.delay(run_id)
    audit_log(payload["username"], "backtest_create", f"run {run_id}")
    return {"run_id": run_id, "task_id": task.id}


@app.get("/api/broker-usage")
def broker_usage(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """通道调用量监控（#37，broker_usage 表聚合）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM broker_usage LIMIT 1")
        except Exception:
            logger.warning("broker_usage: broker_usage 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute(
            "SELECT provider, COUNT(*), COALESCE(AVG(latency_ms),0), "
            "CASE WHEN COUNT(*)>0 THEN round(SUM(CASE WHEN success THEN 1 ELSE 0 END)*100.0/COUNT(*),1) ELSE 0 END "
            "FROM broker_usage WHERE ts::date=current_date GROUP BY provider ORDER BY COUNT(*) DESC")
        today = [{"provider": r[0], "calls": r[1], "avg_latency_ms": int(r[2]), "success_rate": float(r[3])} for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT ts::date AS d, COUNT(*), COALESCE(AVG(latency_ms),0) FROM broker_usage "
            "WHERE ts >= current_date - interval '7 days' GROUP BY d ORDER BY d")
        trend = [{"date": str(r[0]), "calls": r[1], "avg_latency_ms": int(r[2])} for r in cur.fetchall()]
    return {"today": today, "trend": trend}


@app.get("/api/backtest")
def list_backtest_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, strategy_config_id, symbols, mode, status, created_at, finished_at, summary_metrics "
            "FROM backtest_runs ORDER BY id DESC LIMIT 100")
        rows = cur.fetchall()
    return [{"id": r[0], "strategy_config_id": r[1], "symbols": json.loads(r[2]) if r[2] else [],
             "mode": r[3], "status": r[4], "created_at": str(r[5]) if r[5] else None,
             "finished_at": str(r[6]) if r[6] else None,
             "summary": json.loads(r[7]) if r[7] else {}} for r in rows]


@app.get("/api/backtest/{run_id}")
def get_backtest_api(run_id: int,
                     payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, strategy_config_id, symbols, params, mode, status, summary_metrics "
            "FROM backtest_runs WHERE id=%s", (run_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "run 不存在")
        cur = conn.execute(
            "SELECT symbol, status, result FROM backtest_symbols WHERE run_id=%s ORDER BY symbol", (run_id,))
        syms = cur.fetchall()
    return {"id": r[0], "strategy_config_id": r[1], "symbols": json.loads(r[2]),
            "params": json.loads(r[3]), "mode": r[4], "status": r[5],
            "summary": json.loads(r[6]) if r[6] else {},
            "symbols_detail": [{"symbol": s[0], "status": s[1], "result": json.loads(s[2]) if s[2] else {}}
                              for s in syms]}


@app.get("/api/backtest/{run_id}/summary")
def backtest_summary(run_id: int, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """回测组汇总：标的绩效平均+排名（#22）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT symbol, result FROM backtest_symbols WHERE run_id=%s AND status='done'", (run_id,))
        rows = cur.fetchall()
    metrics_keys = ["total_return_pct", "win_rate", "max_drawdown_pct", "sharpe_ratio", "total_trades"]
    results = []
    for sym, result_json in rows:
        r = json.loads(result_json) if result_json else {}
        results.append({"symbol": sym, **{k: r.get(k, 0) for k in metrics_keys}})
    ranked = sorted(results, key=lambda x: x.get("total_return_pct", 0), reverse=True)
    avg = {k: round(sum(r[k] for r in results) / len(results), 3) for k in metrics_keys} if results else {}
    return {"run_id": run_id, "count": len(results), "avg": avg, "ranked": ranked}


@app.get("/api/backtest/{run_id}/{symbol}/stream")
def backtest_stream_api(run_id: int, symbol: str,
                        payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """SSE 单标的实时（轮询 Valkey backtest:run:{run_id}:{symbol}）。"""
    from fastapi.responses import StreamingResponse
    r = redis.Redis(connection_pool=_redis_pool)
    key = f"backtest:run:{run_id}:{symbol}"

    async def gen():
        for _ in range(720):  # 最多 6 分钟
            done = r.get(key + ":done")
            if done:
                yield f"data: {done}\n\n"
                break
            err = r.get(key + ":error")
            if err:
                yield f"data: {json.dumps({'error': err})}\n\n"
                break
            frame = r.get(key)
            if frame:
                yield f"data: {frame}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- WS 流式聊天（D1 #24）---

@app.websocket("/ws/market")
async def ws_market(ws, token: str = Query(...)):
    """P3-18 WS 行情推送（占位，实盘后推送实时行情）。"""
    from .auth import verify_jwt
    try:
        payload = verify_jwt(token)
    except Exception:
        await ws.close(code=4001, reason="token 无效")
        return
    await ws.accept()
    try:
        while True:
            await ws.send_json({"type": "ping", "msg": "行情 WS 待实盘数据接入"})
            await asyncio.sleep(30)
    except Exception:
        pass


@app.websocket("/ws/chat")
async def ws_chat(ws, token: str = Query(...)):
    """WS 流式聊天（token query 认证，role 从 JWT 取非客户端，P0-3 修复）。"""
    from .auth import verify_jwt
    try:
        payload = verify_jwt(token)
    except Exception:
        await ws.close(code=4001, reason="token 无效")
        return
    role = payload.get("role", "viewer")
    await ws.accept()
    from src.llm_gateway import gateway
    try:
        while True:
            data = await ws.receive_json()
            messages = data.get("messages", [])
            async for chunk in gateway.chat_stream(messages, role=role, caller="web_chat"):
                await ws.send_text(chunk)
            await ws.send_text("[DONE]")
    except Exception:
        pass
