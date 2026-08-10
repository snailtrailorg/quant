"""Web 后端 · FastAPI 应用 + API 端点。

启动: uvicorn src.web_api.main:app --reload --port 8000
"""

from __future__ import annotations
from src.data_platform.db import get_conn
import os
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends, Header, Query, Body
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
    """当前持仓（account_snapshot 总资产 + trade_log 累计持仓，#6）。"""
    with get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS account_snapshot (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), total_value NUMERIC, daily_pnl NUMERIC DEFAULT 0, initial_capital NUMERIC DEFAULT 1000000)")
        cur = conn.execute("SELECT total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 1")
        snap = cur.fetchone()
        conn.execute("CREATE TABLE IF NOT EXISTS trade_log (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), order_id BIGINT, symbol TEXT, action TEXT, volume INT, price NUMERIC, commission NUMERIC)")
        cur = conn.execute("SELECT symbol, COALESCE(SUM(CASE WHEN action='BUY' THEN volume ELSE -volume END),0) FROM trade_log GROUP BY symbol")
        positions = [{"symbol": r[0], "volume": int(r[1])} for r in cur.fetchall() if r[1] and r[1] != 0]
    total_value = float(snap[0]) if snap else 0
    initial = float(snap[2]) if snap and snap[2] else 1000000
    total_pnl = (total_value - initial) if snap else 0
    return {"positions": positions, "total_value": total_value, "total_pnl": total_pnl, "total_pnl_pct": round(total_pnl/initial*100, 2) if initial else 0}


@app.get("/api/pnl")
def get_pnl(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """盈亏曲线（account_snapshot 时间序列，#6）。"""
    with get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS account_snapshot (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), total_value NUMERIC, daily_pnl NUMERIC DEFAULT 0, initial_capital NUMERIC DEFAULT 1000000)")
        cur = conn.execute("SELECT ts, total_value, daily_pnl FROM account_snapshot ORDER BY ts DESC LIMIT 90")
        rows = cur.fetchall()
    curve = [{"ts": str(r[0])[:19], "value": float(r[1]) if r[1] else 0, "daily_pnl": float(r[2]) if r[2] else 0} for r in reversed(rows)]
    today_pnl = curve[-1]["daily_pnl"] if curve else 0
    initial = 1000000
    total_pnl = (curve[-1]["value"] - initial) if curve else 0
    return {"curve": curve, "today_pnl": today_pnl, "total_pnl": total_pnl, "total_pnl_pct": round(total_pnl/initial*100, 2)}


@app.get("/api/orders")
def get_orders(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """订单记录（order_log 最近 100，#6）。"""
    with get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS order_log (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), strategy_id TEXT, symbol TEXT, action TEXT, volume INT, price NUMERIC, status TEXT DEFAULT 'submitted')")
        cur = conn.execute("SELECT ts, strategy_id, symbol, action, volume, price, status FROM order_log ORDER BY ts DESC LIMIT 100")
        rows = cur.fetchall()
    return {"orders": [{"ts": str(r[0])[:19], "strategy_id": r[1], "symbol": r[2], "action": r[3], "volume": r[4], "price": float(r[5]) if r[5] else 0, "status": r[6]} for r in rows], "total": len(rows)}


@app.get("/api/dashboard")
def get_dashboard(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """Dashboard 量化指标（account_snapshot + 回测绩效，#10）。"""
    with get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS account_snapshot (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), total_value NUMERIC, daily_pnl NUMERIC DEFAULT 0, initial_capital NUMERIC DEFAULT 1000000)")
        cur = conn.execute("SELECT total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 1")
        snap = cur.fetchone()
        cur = conn.execute("SELECT COUNT(*) FROM backtest_runs WHERE status='done'")
        bt = cur.fetchone()
    total_value = float(snap[0]) if snap else 0
    initial = float(snap[2]) if snap and snap[2] else 1000000
    total_pnl = (total_value - initial) if snap else 0
    return {"total_value": total_value, "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl / initial * 100, 2) if (snap and initial) else 0,
            "daily_pnl": float(snap[1]) if snap else 0, "backtest_count": bt[0]}


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


@app.post("/api/chat")
def chat(req: ChatReq, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """自然语言查询 → LLM 网关（只读工具）。"""
    try:
        from src.llm_gateway import gateway
        from src.llm_gateway.gateway import READ_TOOLS
        resp = gateway.chat(
            messages=[{"role": "user", "content": req.message}],
            tools=READ_TOOLS,
            role=payload["role"],
            timeout=30,
            retries=0,
            caller="web_chat",
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
    import uuid
    session_id = str(uuid.uuid4())
    from src.feishu_bot.tasks import feishu_register_task
    feishu_register_task.delay(session_id)
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
    import os, redis
    r = redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
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
    import os, redis
    r = redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
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
    import pandas as pd
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
                      payload: dict = Depends(require_perm("strategy_control"))):
    """更新预算配置。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE llm_budget SET provider=%s, daily_token_limit=%s, monthly_cost_limit=%s, "
            "alert_threshold_pct=%s, enabled=%s, updated_at=now() WHERE id=%s",
            (req.provider, req.daily_token_limit, req.monthly_cost_limit,
             req.alert_threshold_pct, req.enabled, bid),
        )
        conn.commit()
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
def list_factors_api(category: str | None = None,
                     payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.strategy_framework.factor import list_factors
    return {"items": list_factors(category)}


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
        conn.execute("""CREATE TABLE IF NOT EXISTS pools (
            id TEXT PRIMARY KEY, name TEXT, category TEXT, description TEXT, created_at TIMESTAMPTZ DEFAULT now())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pool_symbols (
            id BIGSERIAL PRIMARY KEY, pool_id TEXT REFERENCES pools(id) ON DELETE CASCADE,
            symbol TEXT, UNIQUE(pool_id, symbol))""")
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
        conn.execute("""CREATE TABLE IF NOT EXISTS pools (
            id TEXT PRIMARY KEY, name TEXT, category TEXT, description TEXT, created_at TIMESTAMPTZ DEFAULT now())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pool_symbols (
            id BIGSERIAL PRIMARY KEY, pool_id TEXT REFERENCES pools(id) ON DELETE CASCADE,
            symbol TEXT, UNIQUE(pool_id, symbol))""")
        conn.execute(
            "INSERT INTO pools (id, name, category, description) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, category=EXCLUDED.category, description=EXCLUDED.description",
            (req.id, req.name, req.category, req.description))
        conn.execute("DELETE FROM pool_symbols WHERE pool_id=%s", (req.id,))
        for sym in symbols:
            conn.execute("INSERT INTO pool_symbols (pool_id, symbol) VALUES (%s,%s) ON CONFLICT DO NOTHING", (req.id, sym))
        conn.commit()
    return {"ok": True, "id": req.id, "count": len(symbols)}


@app.delete("/api/pool/{pid}")

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
        conn.execute("""CREATE TABLE IF NOT EXISTS strategy_account (
            id BIGSERIAL PRIMARY KEY, strategy_id TEXT, account_id TEXT, broker_provider TEXT,
            initial_capital NUMERIC DEFAULT 1000000, leverage INT DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT now(), UNIQUE(strategy_id, account_id))""")
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
        conn.execute("""CREATE TABLE IF NOT EXISTS strategy_account (
            id BIGSERIAL PRIMARY KEY, strategy_id TEXT, account_id TEXT, broker_provider TEXT,
            initial_capital NUMERIC DEFAULT 1000000, leverage INT DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT now(), UNIQUE(strategy_id, account_id))""")
        conn.execute(
            "INSERT INTO strategy_account (strategy_id, account_id, broker_provider, initial_capital, leverage) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (strategy_id, account_id) DO UPDATE SET "
            "broker_provider=EXCLUDED.broker_provider, initial_capital=EXCLUDED.initial_capital, leverage=EXCLUDED.leverage",
            (req.strategy_id, req.account_id, req.broker_provider, req.initial_capital, req.leverage))
        conn.commit()
    return {"ok": True}


@app.delete("/api/strategy_account/{said}")
def unbind_strategy_account(said: int, payload: dict = Depends(require_perm("strategy_control"))):
    """解绑策略-账户（#27）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM strategy_account WHERE id=%s", (said,))
        conn.commit()
    return {"ok": True}


def delete_pool(pid: str, payload: dict = Depends(require_perm("strategy_control"))):
    """删除标的池（CASCADE 删 symbols，#22）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM pools WHERE id=%s", (pid,))
        conn.commit()
    return {"ok": True}


@app.post("/api/backtest")
def create_backtest_api(body: dict = Body(...),
                        payload: dict = Depends(require_perm("strategy_control"))):
    """启动回测 run：写 backtest_runs + Celery backtest_run_task。"""
    import json
    strategy_id = body.get("strategy_config_id")
    symbols = body.get("symbols", [])
    pool_id = body.get("pool_id")
    if pool_id:
        with get_conn() as conn:
            cur = conn.execute("SELECT symbol FROM pool_symbols WHERE pool_id=%s", (pool_id,))
            symbols = [r[0] for r in cur.fetchall()]
    if not symbols or not strategy_id:
        return {"error": "需 strategy_config_id + symbols/pool_id"}
    params = body.get("params", {})
    mode = body.get("mode", "single")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO backtest_runs (strategy_config_id, symbols, params, mode, status) "
            "VALUES (%s,%s,%s,%s,'pending') RETURNING id",
            (strategy_id, json.dumps(symbols), json.dumps(params), mode))
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
        conn.execute("""CREATE TABLE IF NOT EXISTS broker_usage (
            id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(),
            provider TEXT, action TEXT, symbol TEXT, success BOOLEAN, latency_ms INT)""")
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
    import json
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
    import json
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, strategy_config_id, symbols, params, mode, status, summary_metrics "
            "FROM backtest_runs WHERE id=%s", (run_id,))
        r = cur.fetchone()
        if not r:
            return {"error": "run 不存在"}
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
    import json
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
    import os, redis, json, asyncio
    from fastapi.responses import StreamingResponse
    r = redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
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

@app.websocket("/ws/chat")
async def ws_chat(ws):
    """WS 流式聊天：gateway.chat_stream 流式推。caller=web_chat。"""
    await ws.accept()
    from src.llm_gateway import gateway
    try:
        while True:
            data = await ws.receive_json()
            messages = data.get("messages", [])
            role = data.get("role", "viewer")
            async for chunk in gateway.chat_stream(messages, role=role, caller="web_chat"):
                await ws.send_text(chunk)
            await ws.send_text("[DONE]")
    except Exception:
        pass
