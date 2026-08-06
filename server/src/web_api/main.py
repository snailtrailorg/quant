"""Web 后端 · FastAPI 应用 + API 端点。

启动: uvicorn src.web_api.main:app --reload --port 8000
"""

from __future__ import annotations
from src.data_platform.db import get_conn
import os
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from .auth import (
    create_jwt, authenticate, create_user, require_role, require_perm,
    audit_log, ensure_default_admin, init_users_table, PERMISSIONS,
    invite_user, register_user, forgot_password, reset_password, change_password, verify_token,
)
from .email_service import send_invite_email, send_password_reset_email

app = FastAPI(title="量化交易平台 API", version="0.1.0")
from src.feishu_bot.router import router as feishu_router
app.include_router(feishu_router)

# CORS（前端 Vue3 开发用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产改具体域名
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

class RegisterReq(BaseModel):
    token: str
    username: str
    password: str

class ForgotReq(BaseModel):
    email: str

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


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


# ——— 认证 ———

@app.post("/api/auth/login")
def login(req: LoginReq):
    user = authenticate(req.username, req.password)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    token = create_jwt(str(user["id"]), user["username"], user["role"])
    audit_log(user["username"], "login")
    return {"token": token, "role": user["role"], "username": user["username"]}


@app.get("/api/auth/me")
def me(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    return {"user_id": payload["sub"], "username": payload["username"], "role": payload["role"],
            "permissions": list(PERMISSIONS.get(payload["role"], set()))}


@app.post("/api/auth/logout")
def logout(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    audit_log(payload["username"], "logout")
    return {"ok": True}


# ——— 邀请制用户管理 ———

@app.post("/api/auth/invite")
async def invite_user_api(req: InviteReq, payload: dict = Depends(require_perm("user_mgmt"))):
    """admin 邀请：填 email 发邀请邮件（默认 Viewer）。"""
    token = invite_user(req.email)
    if not token:
        raise HTTPException(400, "该邮箱已注册或邀请失败")
    await send_invite_email(req.email, token)
    audit_log(payload["username"], "invite_user", req.email)
    return {"status": "invited", "email": req.email}


@app.get("/api/auth/invite/verify")
def verify_invite_token(token: str):
    """验证 invite token 有效性（前端开通页用）。"""
    t = verify_token(token, "invite")
    if not t:
        raise HTTPException(400, "token 无效或已过期")
    return {"valid": True, "email": t["email"]}


@app.post("/api/auth/register")
def register_api(req: RegisterReq):
    """自助开通：凭 invite token 建用户（默认 Viewer）。"""
    user = register_user(req.token, req.username, req.password)
    if not user:
        raise HTTPException(400, "token 无效/已用/过期，或用户名已存在")
    audit_log(user["username"], "self_register")
    return {"status": "registered", "username": user["username"], "email": user["email"]}


@app.post("/api/auth/forgot-password")
async def forgot_password_api(req: ForgotReq):
    """找回密码：发重置邮件（不泄露 email 是否存在）。"""
    token = forgot_password(req.email)
    if not token:
        return {"status": "sent"}  # email 不存在也返回 sent（防枚举）
    await send_password_reset_email(req.email, token)
    return {"status": "sent"}


@app.post("/api/auth/reset-password")
def reset_password_api(req: ResetReq):
    """凭 reset token 重置密码。"""
    ok = reset_password(req.token, req.new_password)
    if not ok:
        raise HTTPException(400, "token 无效/已用/过期")
    return {"status": "reset"}


@app.post("/api/auth/change-password")
def change_password_api(req: ChangePwdReq, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """改密码：需旧密码验证。"""
    ok = change_password(int(payload["sub"]), req.old_password, req.new_password)
    if not ok:
        raise HTTPException(400, "旧密码错误")
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
        raise HTTPException(409, str(e))


@app.get("/api/user")
def list_users(payload: dict = Depends(require_perm("user_mgmt"))):
    import psycopg
    with get_conn() as conn:
        cur = conn.execute("SELECT id, username, role, enabled, email, email_verified, created_at FROM users ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "username": r[1], "role": r[2], "enabled": r[3],
             "email": r[4], "email_verified": r[5], "created_at": str(r[6])} for r in rows]


# --- 策略管理（DB 驱动） ---

@app.get("/api/strategy")
def list_strategies(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列策略配置（从 DB 读）。"""
    import psycopg, json
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, type, symbol, adapter, enabled, factors, aggregator, backtest_verified FROM strategy_config ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "symbol": r[3], "adapter": r[4],
             "enabled": r[5], "factors": r[6], "aggregator": r[7], "backtest_verified": r[8]} for r in rows]


@app.post("/api/strategy")
def create_strategy(req: StrategyConfig, payload: dict = Depends(require_perm("strategy_control"))):
    """新建策略配置。"""
    import psycopg, json
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
    """更新策略配置（含因子校验）。"""
    import psycopg, json
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
    """启动策略。未通过回测验证禁止实盘（EXE-003）。"""
    import psycopg
    with get_conn() as conn:
        cur = conn.execute("SELECT backtest_verified FROM strategy_config WHERE id=%s", (sid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "策略不存在")
        if not row[0]:
            raise HTTPException(403, "策略未通过回测验证，禁止实盘。请先运行回测。")
        conn.execute("UPDATE strategy_config SET enabled=true WHERE id=%s", (sid,))
        conn.commit()
    audit_log(payload["username"], "strategy_start", sid)
    return {"id": sid, "status": "running"}


@app.post("/api/strategy/{sid}/stop")
def stop_strategy(sid: str, payload: dict = Depends(require_perm("strategy_control"))):
    import psycopg
    with get_conn() as conn:
        conn.execute("UPDATE strategy_config SET enabled=false WHERE id=%s", (sid,))
        conn.commit()
    audit_log(payload["username"], "strategy_stop", sid)
    return {"id": sid, "status": "stopped"}


@app.post("/api/strategy/{sid}/verify")
def verify_strategy(sid: str, payload: dict = Depends(require_perm("strategy_control"))):
    """标记策略已通过回测验证。"""
    import psycopg
    with get_conn() as conn:
        conn.execute("UPDATE strategy_config SET backtest_verified=true WHERE id=%s", (sid,))
        conn.commit()
    audit_log(payload["username"], "verify_strategy", sid, detail="回测验证通过")
    return {"id": sid, "backtest_verified": True}

# ——— 持仓/盈亏 ———

@app.get("/api/position")
def get_position(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """当前持仓（占位，实盘后接真实数据）。"""
    return {"positions": [], "total_value": 0, "total_pnl": 0, "total_pnl_pct": 0}


@app.get("/api/pnl")
def get_pnl(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """盈亏曲线（占位）。"""
    return {"curve": [], "today_pnl": 0, "total_pnl": 0, "total_pnl_pct": 0}


@app.get("/api/orders")
def get_orders(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """订单记录（占位）。"""
    return {"orders": [], "total": 0}


# ——— 账户管理（Admin） ———

@app.get("/api/account")
def list_accounts(payload: dict = Depends(require_perm("account_keys"))):
    """列券商/交易所账户（密钥不返回明文）。"""
    import psycopg
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id SERIAL PRIMARY KEY, name TEXT, exchange TEXT NOT NULL,
                api_key_hint TEXT, enabled BOOLEAN DEFAULT true, created_at TIMESTAMPTZ DEFAULT now()
            )""")
        cur = conn.execute("SELECT id, name, exchange, api_key_hint, enabled, created_at FROM accounts ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "exchange": r[2], "api_key_hint": r[3],
             "enabled": r[4], "created_at": str(r[5])} for r in rows]


@app.get("/api/account/{aid}")
def get_account(aid: int, payload: dict = Depends(require_perm("account_keys"))):
    import psycopg
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, exchange, api_key_hint, enabled, created_at FROM accounts WHERE id=%s", (aid,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "账户不存在")
    return {"id": row[0], "name": row[1], "exchange": row[2], "api_key_hint": row[3], "enabled": row[4]}


# ——— 日志/告警 ———

@app.get("/api/log")
def get_logs(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """运行日志（占位，接真实日志存储）。"""
    return {"logs": [
        {"ts": "2026-07-24T14:00:00", "level": "INFO", "module": "risk", "msg": "风控扫描正常"},
        {"ts": "2026-07-24T13:59:00", "level": "INFO", "module": "data", "msg": "日线增量完成"},
        {"ts": "2026-07-24T13:00:00", "level": "WARN", "module": "strategy", "msg": "双低轮动触发"},
    ]}


@app.get("/api/alert")
def get_alerts(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """告警历史（从 Valkey 读取）。"""
    import redis, os, json
    r = redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    ids = r.lrange("alert:history", 0, 99)
    alerts = []
    for aid in ids:
        data = r.hgetall(f"alert:{aid}")
        if data:
            alerts.append({"id": aid, **data})
    return {"alerts": alerts, "total": len(alerts)}


# ——— 自然语言查询 ———

class ChatReq(BaseModel):
    message: str
    tier: str = "regular"


@app.post("/api/chat")
def chat(req: ChatReq, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """自然语言查询 → LLM 网关（只读工具）。"""
    try:
        from src.llm_gateway import gateway
        from src.llm_gateway.gateway import READ_TOOLS
        resp = gateway.chat(
            messages=[{"role": "user", "content": req.message}],
            tier=req.tier,
            tools=READ_TOOLS,
            role=payload["role"],
            timeout=30,
            retries=0,
        )
        return {"reply": resp.content or "（LLM 无响应，请检查 API key）", "usage": resp.usage}
    except Exception as e:
        return {"reply": f"（LLM 暂不可用: {e}）", "usage": {}}


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
    max_tokens: int | None = None
    temperature: float | None = None
    tier: str = "regular"
    priority: int = 10
    enabled: bool = False


@app.get("/api/llm-models")
def list_llm_models(payload: dict = Depends(require_perm("llm_config"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, provider, model, api_key_encrypted, base_url, context_window, supports_tools, max_tokens, temperature, tier, priority, enabled FROM llm_model_config ORDER BY tier, priority")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "provider": r[2], "model": r[3], "has_key": bool(r[4]), "base_url": r[5], "context_window": r[6], "supports_tools": r[7], "max_tokens": r[8], "temperature": r[9], "tier": r[10], "priority": r[11], "enabled": r[12]} for r in rows]


@app.post("/api/llm-models")
def create_llm_model(req: LLMModelReq, payload: dict = Depends(require_perm("llm_config"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.api_key) if req.api_key else ""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO llm_model_config (name, provider, model, api_key_encrypted, base_url, context_window, supports_tools, max_tokens, temperature, tier, priority, enabled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (req.name, req.provider, req.model, enc, req.base_url, req.context_window, req.supports_tools, req.max_tokens, req.temperature, req.tier, req.priority, req.enabled))
        conn.commit()
    audit_log(payload["username"], "llm_model_create", detail=f"{req.provider}/{req.model}")
    return {"id": cur.fetchone()[0]}


@app.put("/api/llm-models/{mid}")
def update_llm_model(mid: int, req: LLMModelReq, payload: dict = Depends(require_perm("llm_config"))):
    from src.web_api.crypto_utils import encrypt
    enc = encrypt(req.api_key) if req.api_key else None
    with get_conn() as conn:
        if enc is not None:
            conn.execute("UPDATE llm_model_config SET name=%s, provider=%s, model=%s, api_key_encrypted=%s, base_url=%s, context_window=%s, supports_tools=%s, max_tokens=%s, temperature=%s, tier=%s, priority=%s, enabled=%s, updated_at=now() WHERE id=%s",
                (req.name, req.provider, req.model, enc, req.base_url, req.context_window, req.supports_tools, req.max_tokens, req.temperature, req.tier, req.priority, req.enabled, mid))
        else:
            conn.execute("UPDATE llm_model_config SET name=%s, provider=%s, model=%s, base_url=%s, context_window=%s, supports_tools=%s, max_tokens=%s, temperature=%s, tier=%s, priority=%s, enabled=%s, updated_at=now() WHERE id=%s",
                (req.name, req.provider, req.model, req.base_url, req.context_window, req.supports_tools, req.max_tokens, req.temperature, req.tier, req.priority, req.enabled, mid))
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
    """列所有飞书机器人（含 role/lang/description）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, app_id, app_secret_encrypted, role, lang, description, enabled, updated_at FROM feishu_config ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "app_id": r[2], "has_secret": bool(r[3]), "role": r[4], "lang": r[5], "description": r[6], "enabled": r[7], "updated_at": r[8]} for r in rows]


class FeishuConnectReq(BaseModel):
    lang: str | None = None  # 浏览器语言（navigator.language），存 feishu_config.lang

@app.post("/api/feishu/connect")
def feishu_connect(req: FeishuConnectReq, payload: dict = Depends(require_perm("feishu_config"))):
    """扫码创建/连接飞书机器人。lang=浏览器缺省语言。"""
    import uuid
    session_id = str(uuid.uuid4())
    from src.feishu_bot.tasks import feishu_register_task
    feishu_register_task.delay(session_id, req.lang)
    return {"session_id": session_id}


@app.get("/api/feishu/status/{session_id}")
def feishu_status(session_id: str, payload: dict = Depends(require_perm("feishu_config"))):
    import os, redis, json
    r = redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/4"), decode_responses=True)
    data = r.get(f"feishu:session:{session_id}")
    if not data:
        return {"status": "pending"}
    return json.loads(data)


@app.post("/api/feishu/{fid}/start")
def feishu_start(fid: int, payload: dict = Depends(require_perm("feishu_config"))):
    """启动机器人长连接（systemctl start quant-feishu-bot@<id>，要 polkit）。"""
    import subprocess
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
    import subprocess
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
    lang: str | None = None
    description: str | None = None

@app.put("/api/feishu/{fid}")
def feishu_update(fid: int, req: FeishuUpdateReq, payload: dict = Depends(require_perm("feishu_config"))):
    """改机器人配置（名称/角色/语言/备注）。修改后 role/lang 对后续消息生效。"""
    with get_conn() as conn:
        if req.name is not None:
            conn.execute("UPDATE feishu_config SET name=%s, updated_at=now() WHERE id=%s", (req.name, fid))
        if req.role is not None:
            conn.execute("UPDATE feishu_config SET role=%s, updated_at=now() WHERE id=%s", (req.role, fid))
        if req.lang is not None:
            conn.execute("UPDATE feishu_config SET lang=%s, updated_at=now() WHERE id=%s", (req.lang, fid))
        if req.description is not None:
            conn.execute("UPDATE feishu_config SET description=%s, updated_at=now() WHERE id=%s", (req.description, fid))
        conn.commit()
    audit_log(payload["username"], "feishu_update", detail=f"id={fid}")
    return {"ok": True}


@app.delete("/api/feishu/{fid}")
def feishu_delete(fid: int, payload: dict = Depends(require_perm("feishu_config"))):
    """删除机器人配置。"""
    import subprocess
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
    import requests
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
