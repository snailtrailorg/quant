"""聊天/LLM · 路由（自然语言查询、WS 流式、LLM 模型/用量/预算、A股选股）"""

from __future__ import annotations
import asyncio
import logging
import json
from fastapi import APIRouter, Depends, Request, Body, WebSocket, WebSocketDisconnect, Query
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (ChatReq, LLMModelReq, LlmBudgetReq)
from src.data_platform.db import get_conn

logger = logging.getLogger("web_api")

router = APIRouter(tags=["chat"])


# ——— 自然语言查询 ———


@router.post("/api/chat")
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


# ——— WS 流式聊天 ———


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, token: str = Query(None)):
    """WS 流式聊天（P0-8/P2 修复：ws 注解补齐；token 支持 query 或首帧 {"type":"auth","token"}
    双通道——前端 AIChat 发首帧 auth 不带 query，原契约只认 query 导致流式握手必败静默降级 POST）。"""
    from ..auth import verify_jwt
    await ws.accept()
    if not token:
        # 首帧认证（5s 超时防挂连接）
        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=5)
            if first.get("type") == "auth":
                token = first.get("token")
        except Exception:
            token = None
    try:
        payload = verify_jwt(token or "")
    except Exception:
        await ws.close(code=4001, reason="token 无效")
        return
    role = payload.get("role", "viewer")
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


@router.websocket("/ws/market")
async def ws_market(ws: WebSocket, token: str = Query(...)):
    """P3-18 WS 行情推送（占位，实盘后推送实时行情）。
    P0-8：ws 参数缺 WebSocket 注解时被当 query 参数--accept() 对 str 调用必炸。"""
    from ..auth import verify_jwt
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


# ——— LLM 模型配置（DB 化，Admin 管理） ———


@router.get("/api/llm-models")
def list_llm_models(payload: dict = Depends(require_perm("llm_config"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, provider, model, api_key_encrypted, base_url, context_window, supports_tools, max_input_tokens, max_output_tokens, temperature, priority, enabled FROM llm_model_config ORDER BY priority")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "provider": r[2], "model": r[3], "has_key": bool(r[4]), "base_url": r[5], "context_window": r[6], "supports_tools": r[7], "max_input_tokens": r[8], "max_output_tokens": r[9], "temperature": r[10], "priority": r[11], "enabled": r[12]} for r in rows]


@router.post("/api/llm-models")
def create_llm_model(req: LLMModelReq, payload: dict = Depends(require_perm("llm_config"))):
    from src.quant_common.crypto import encrypt
    enc = encrypt(req.api_key) if req.api_key else ""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO llm_model_config (name, provider, model, api_key_encrypted, base_url, context_window, supports_tools, max_input_tokens, max_output_tokens, temperature, priority, enabled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (req.name, req.provider, req.model, enc, req.base_url, req.context_window, req.supports_tools, req.max_input_tokens, req.max_output_tokens, req.temperature, req.priority, req.enabled))
        conn.commit()
    audit_log(payload["username"], "llm_model_create", detail=f"{req.provider}/{req.model}")
    return {"id": cur.fetchone()[0]}


@router.post("/api/llm-models/{mid}")
def update_llm_model(mid: int, req: LLMModelReq, payload: dict = Depends(require_perm("llm_config"))):
    from src.quant_common.crypto import encrypt
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


@router.delete("/api/llm-models/{mid}")
def delete_llm_model(mid: int, payload: dict = Depends(require_perm("llm_config"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM llm_model_config WHERE id=%s", (mid,))
        conn.commit()
    audit_log(payload["username"], "llm_model_delete", detail=f"id={mid}")
    from src.llm_gateway.gateway import gateway
    gateway.reload_models()
    return {"id": mid}


@router.post("/api/llm-models/{mid}/test")
def test_llm_model(mid: int, payload: dict = Depends(require_perm("llm_config"))):
    from src.quant_common.crypto import decrypt
    from openai import OpenAI
    with get_conn() as conn:
        cur = conn.execute("SELECT api_key_encrypted, base_url, model FROM llm_model_config WHERE id=%s", (mid,))
        r = cur.fetchone()
    if not r or not r[0]:
        from fastapi import HTTPException
        raise HTTPException(400, "模型未配置 api_key")
    try:
        client = OpenAI(api_key=decrypt(r[0]), base_url=r[1], timeout=15)
        resp = client.chat.completions.create(model=r[2], messages=[{"role": "user", "content": "ping"}], max_tokens=5)
        return {"ok": True, "reply": resp.choices[0].message.content}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ——— LLM 用量 ——


@router.get("/api/llm-usage/summary")
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


# ——— LLM 预算 ——


@router.get("/api/llm-budget")
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


@router.post("/api/llm-budget/check")
def check_budget(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """手动触发预算告警检查。"""
    from src.llm_gateway.budget import check_budget_alerts
    result = check_budget_alerts()
    return result


@router.post("/api/llm-budget/{bid}")
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


# ——— A 股分析 ———


@router.get("/api/astock/selection")
def astock_selection(date: str = "", payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """当日选股结果（横截面：一档表全市场一次 SQL）。date=YYYYMMDD 历史截面。"""
    import re
    from src.astock_analysis import DailySelectionEngine
    if date and not re.fullmatch(r"\d{8}", date):
        raise ApiError(400, "PARAM_INVALID", "date 需为 YYYYMMDD")
    engine = DailySelectionEngine(top_n=20)
    results = engine.run(trade_date=date or None)
    return [{"symbol": r.symbol, "vt_symbol": r.vt_symbol, "score": r.score,
             "rating": r.rating, "support": r.support, "resistance": r.resistance,
             "conclusion": r.conclusion} for r in results]