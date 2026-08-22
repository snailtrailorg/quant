"""策略/因子/策略账户路由（从 main.py 提取）。"""

from __future__ import annotations
import json
import subprocess
from fastapi import APIRouter, Depends, Request, Body, HTTPException
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (LoginReq, UserCreate, StrategyConfig, InviteReq, RegisterReq, ForgotReq, ResetReq, ChangePwdReq, LogAnalyzeReq, ChatReq, LLMModelReq, IMBotCreateReq, IMBotUpdateReq, IMBotUserReq, LlmBudgetReq, DataSourceReq, ChannelReq, BrokerReq, RiskRuleReq, PoolReq, StrategyAccountReq)
from src.data_platform.db import get_conn
import logging
logger = logging.getLogger("web_api")

router = APIRouter(tags=["strategy"])


def _validate_strategy_category(stype: str, symbol: str, factors: list) -> dict:
    """#10 品类校验：symbol 空时按 type 推断（convertible→cb / etf→etf / 其余→astock）。"""
    from src.strategy_framework.factor import validate_strategy_factors
    sym = symbol or ""
    if not sym:
        t = (stype or "").lower()
        cat = "cb" if "convertible" in t or "cb" in t else ("etf" if "etf" in t else "astock")
        sym = {"cb": "113000.SHSE", "etf": "510300.SHSE", "astock": "600000.SHSE"}[cat]  # 探测代号
    return validate_strategy_factors(sym, factors)


# --- 策略管理（DB 驱动） ---

@router.get("/api/strategy")
def list_strategies(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列策略配置（从 DB 读）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, type, symbol, adapter, enabled, factors, aggregator, risk, params, backtest_verified FROM strategy_config ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "symbol": r[3], "adapter": r[4],
             "enabled": r[5], "factors": r[6], "aggregator": r[7], "risk": r[8], "params": r[9], "backtest_verified": r[10]} for r in rows]


@router.post("/api/strategy")
def create_strategy(req: StrategyConfig, payload: dict = Depends(require_perm("strategy_control"))):
    """新建策略配置。"""
    # 链条打磨#10：品类校验（create 此前完全无校验；update 用空 symbol 假阴性）
    _v = _validate_strategy_category(req.type, req.symbol, req.factors)
    if not _v["valid"]:
        raise ApiError(400, "FACTOR_INCOMPATIBLE", _v["message"])
    # P0-1 修复（双盲审计 F1.2 + 复审修正）：键名实为 python_code、判据用 params.mode
    # （原 type=="python" 恒假 + 读 "code" 键 → 校验整体 no-op）
    if (req.params or {}).get("mode") == "python":
        from src.strategy_framework.strategy import _check_ast_blacklist
        _forbidden = _check_ast_blacklist((req.params or {}).get("python_code", ""))
        if _forbidden:
            raise ApiError(400, "CODE_FORBIDDEN", f"策略代码安全校验失败: {_forbidden}")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO strategy_config (id, name, type, symbol, adapter, enabled, factors, aggregator, risk, params) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (req.id, req.name, req.type, req.symbol, req.adapter, req.enabled,
             json.dumps(req.factors), json.dumps(req.aggregator), json.dumps(req.risk), json.dumps(req.params)))
        conn.commit()
    audit_log(payload["username"], "create_strategy", req.id, json.dumps({"name": req.name}))
    return {"id": req.id, "status": "created"}


@router.post("/api/strategy/validate-python")
def validate_python_code(code: dict = Body(...), payload: dict = Depends(require_role("analyst", "trader", "admin"))):
    """校验 Python 策略代码：语法检查 + AST 安全校验（#15）。"""
    from src.strategy_framework.strategy import _check_ast_blacklist
    code_str = code.get("code", "")
    forbidden = _check_ast_blacklist(code_str)
    if forbidden:
        return {"valid": False, "error": forbidden}
    return {"valid": True}

@router.post("/api/strategy/validate-params")
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


@router.post("/api/strategy/{sid}")
def update_strategy(sid: str, req: StrategyConfig, payload: dict = Depends(require_perm("strategy_control"))):
    """更新策略配置（含因子校验；Python 模式跳过因子校验）。"""
    # Python 模式（#15）跳过因子校验；#10：symbol 空时查旧值（UI 恒空 → 此前 detect 假阴性）
    if req.params.get("mode") != "python":
        _sym = req.symbol
        if not _sym:
            with get_conn() as conn:
                _r = conn.execute("SELECT symbol FROM strategy_config WHERE id=%s", (sid,)).fetchone()
                _sym = _r[0] if _r else ""
        _v = _validate_strategy_category(req.type, _sym, req.factors)
        if not _v["valid"]:
            raise ApiError(400, "FACTOR_INCOMPATIBLE", _v["message"])
    else:
        # P0-1 修复（双盲审计 F1.2 + 复审修正）：读 python_code 键（原 "code" 恒空 → no-op）
        from src.strategy_framework.strategy import _check_ast_blacklist
        _forbidden = _check_ast_blacklist(req.params.get("python_code", ""))
        if _forbidden:
            raise ApiError(400, "CODE_FORBIDDEN", f"策略代码安全校验失败: {_forbidden}")
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


@router.post("/api/strategy/{sid}/start")
def start_strategy(sid: str, payload: dict = Depends(require_perm("strategy_control"))):
    """启动策略。未通过回测验证禁止实盘（EXE-003）。策略必须绑定标的或标的池（F-POOL-003）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT backtest_verified, symbol, params FROM strategy_config WHERE id=%s", (sid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "STRATEGY_NOT_FOUND", "策略不存在")
        if not row[0]:
            raise ApiError(403, "STRATEGY_NOT_VERIFIED", "策略未通过回测验证，禁止实盘。请先运行回测。")
        symbol, params_raw = row[1], row[2]
        params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or {})
        # F-POOL-003：策略必须绑定标的或标的池
        if not symbol and not params.get("pool_id"):
            raise ApiError(400, "STRATEGY_NO_SYMBOL", "策略未绑定标的或标的池，禁止启动。请在策略编辑页设置 symbol 或 pool_id。")
        conn.execute("UPDATE strategy_config SET enabled=true WHERE id=%s AND enabled=false AND backtest_verified=true", (sid,))
        conn.commit()
    audit_log(payload["username"], "strategy_start", sid)
    try:
        subprocess.run(["systemctl", "start", f"quant-strategy@{sid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("start_strategy: systemctl start quant-strategy@%s 失败", sid, exc_info=True)
    return {"id": sid, "status": "running"}


@router.post("/api/strategy/{sid}/stop")
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


@router.post("/api/strategy/{sid}/verify")
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
                raise ApiError(400, "BACKTEST_EVIDENCE_INVALID", f"回测证据无效: run_id={run_id}（须属于该策略且状态 done）")
        else:
            cur = conn.execute(
                "SELECT COUNT(*) FROM backtest_runs WHERE strategy_config_id=%s AND status='done'", (sid,))
            if cur.fetchone()[0] == 0:
                raise ApiError(403, "NO_DONE_BACKTEST", "该策略无已完成回测，禁止标记验证（需真实回测证据，F-44）")
        conn.execute("UPDATE strategy_config SET backtest_verified=true WHERE id=%s", (sid,))
        conn.commit()
    audit_log(payload["username"], "verify_strategy", sid, detail="回测验证通过")
    return {"id": sid, "backtest_verified": True}


# --- 因子 + 三账对账（#2 + #7） ---

@router.get("/api/factors")
def list_factors_api(category: str | None = None, static_only: bool = False,
                     payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.strategy_framework.factor import list_factors
    return {"items": list_factors(category, static_only=static_only)}


@router.post("/api/factors")
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


@router.post("/api/factors/preview")
def preview_factor_api(body: dict = Body(...),
                       payload: dict = Depends(require_role("analyst", "trader", "admin"))):
    """因子试算（链条打磨#5）：真实 bar 喂 compute 看输出序列——写完因子不必搭策略+回测才能看结果。

    body: {code, symbol?, freq?('1D'|'1min'), bars?(默认 60), params?{}}
    返回 {values: [{ts, value}], stats: {min,max,mean,last,count}, error?}
    """
    import math
    from src.strategy_framework.factor import _make_factor_class, BarContext
    from src.data_platform.db import get_bars
    code = body.get("code", "")
    symbol = body.get("symbol", "600000.SHSE")
    freq = body.get("freq", "1D")
    n = max(10, min(int(body.get("bars", 60)), 500))
    params = body.get("params") or {}
    if freq not in ("1D", "1min", "5min"):
        raise ApiError(400, "FACTOR_PREVIEW_FREQ", "freq 仅支持 1D/1min/5min")
    try:
        factor_cls = _make_factor_class("preview", code, params)
        factor = factor_cls()
    except Exception as e:
        return {"error": f"因子编译失败: {str(e)[:200]}"}
    from datetime import datetime as _dt, timedelta as _td
    end, start = _dt.now(), _dt.now() - _td(days=365 if freq == "1D" else 14)
    df = get_bars(symbol, freq, start, end)
    if df is None or df.empty:
        return {"error": f"无数据: {symbol} {freq}"}
    bars = df.tail(n).to_dict("records")
    values, errors = [], 0
    for i, bar in enumerate(bars):
        hist = bars[:i]
        ctx = BarContext(close=float(bar["close"]), high=float(bar["high"]), low=float(bar["low"]),
                         open_=float(bar["open"]), volume=float(bar.get("volume") or 0),
                         history=[{"close": float(h["close"]), "high": float(h["high"]),
                                   "low": float(h["low"]), "open": float(h["open"]),
                                   "volume": float(h.get("volume") or 0)} for h in hist])
        try:
            v = factor.compute(ctx)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                v = None
            values.append({"ts": str(bar["ts"])[:19], "value": round(float(v), 6) if v is not None else None})
        except Exception:
            errors += 1
            values.append({"ts": str(bar["ts"])[:19], "value": None})
    nums = [v["value"] for v in values if v["value"] is not None]
    stats = {"count": len(nums), "errors": errors,
             "min": round(min(nums), 6) if nums else None,
             "max": round(max(nums), 6) if nums else None,
             "mean": round(sum(nums) / len(nums), 6) if nums else None,
             "last": nums[-1] if nums else None}
    return {"values": values, "stats": stats}


@router.post("/api/factors/validate")
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

@router.post("/api/factors/{name}")
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


@router.delete("/api/factors/{name}")
def delete_factor_api(name: str,
                       payload: dict = Depends(require_perm("strategy_control"))):
    """删除自定义因子。链条打磨#4：先查引用（strategy_config.factors JSON）——删掉在用因子策略启动即崩。"""
    from src.strategy_framework.factor import delete_custom_factor
    with get_conn() as conn:
        cur = conn.execute("SELECT id, factors FROM strategy_config")
        used_by = []
        for sid_, fjson in cur.fetchall():
            try:
                fl = json.loads(fjson) if fjson else []
                if any(f.get("name") == name for f in fl):
                    used_by.append(sid_)
            except Exception:
                continue
    if used_by:
        raise ApiError(409, "FACTOR_IN_USE",
                       f"因子 {name} 被策略引用: {', '.join(used_by)}——先在策略中移除再删除")
    ok = delete_custom_factor(name)
    if not ok:
        raise ApiError(404, "FACTOR_NOT_FOUND", f"因子 {name} 不存在或非自定义因子")
    audit_log(payload["username"], "delete_factor", name)
    return {"ok": True}


# --- 策略-账户绑定 ---

@router.get("/api/strategy_account")
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


@router.post("/api/strategy_account")
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


@router.delete("/api/strategy_account/{said}")
def unbind_strategy_account(said: int, payload: dict = Depends(require_perm("strategy_control"))):
    """解绑策略-账户（#27）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM strategy_account WHERE id=%s", (said,))
        conn.commit()
    return {"ok": True}