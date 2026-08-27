"""平台化管理路由：数据源/通道/券商/任务/用量 —— 从 main.py 提取的 mgmt 端点。"""

import json

from fastapi import APIRouter, Depends, Request, Body, HTTPException
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (DataSourceReq, ChannelReq, BrokerReq,
                      PointsTierReq, RateLimitOverrideReq)
from src.data_platform.db import get_conn
import logging

logger = logging.getLogger("web_api")

router = APIRouter(tags=["mgmt"])


# --- 数据源管理（PT3 平台化数据层） ---


@router.get("/api/data-sources")
def list_data_sources(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, provider, name, credentials_encrypted IS NOT NULL, params, usage_limit, enabled, updated_at FROM data_source_config ORDER BY provider")
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "has_credentials": bool(r[3]),
             "params": r[4], "usage_limit": r[5], "enabled": r[6],
             "updated_at": str(r[7]) if r[7] else None} for r in rows]


@router.post("/api/data-sources")
def create_data_source(req: DataSourceReq, payload: dict = Depends(require_role("admin"))):
    from src.quant_common.crypto import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO data_source_config (provider, name, credentials_encrypted, params, usage_limit, enabled) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (req.provider, req.name, enc, req.params, req.usage_limit, req.enabled))
        conn.commit()
    audit_log(payload["username"], "data_source_create", req.provider)
    return {"id": cur.fetchone()[0]}


@router.post("/api/data-sources/{dsid}")
def update_data_source(dsid: int, req: DataSourceReq, payload: dict = Depends(require_role("admin"))):
    from src.quant_common.crypto import encrypt
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


@router.delete("/api/data-sources/{dsid}")
def delete_data_source(dsid: int, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM data_source_config WHERE id=%s", (dsid,))
        conn.commit()
    audit_log(payload["username"], "data_source_delete", f"id={dsid}")
    return {"ok": True}


@router.post("/api/data-sources/{dsid}/test")
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


# --- 积分档四层限流（points_tier 预设 / rate_limits 覆写 / 熔断参数，2026-08-27） ---


def _ds_cls(provider: str):
    """provider → 已注册 DataSource 类（未注册 404）。"""
    from src.data_platform.data_source import _REGISTRY
    cls = _REGISTRY.get(provider)
    if not cls:
        raise ApiError(404, "DS_NOT_REGISTERED", f"provider {provider} 未注册（需实现 DataSource 子类）")
    return cls


def _preset_cls(provider: str):
    """provider → 有积分档预设的 DataSource 类（无预设 404——预设端点专属门槛）。"""
    cls = _ds_cls(provider)
    if not getattr(cls, "POINTS_PRESETS", None):
        raise ApiError(404, "NO_POINTS_PRESETS", f"数据源 {provider} 不支持积分档预设")
    return cls


def _load_ds_params(provider: str) -> tuple[int, dict]:
    """读 provider 配置行（enabled 优先）→ (id, params dict)；无配置 404。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, params FROM data_source_config WHERE provider=%s "
            "ORDER BY enabled DESC, id LIMIT 1", (provider,))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "DS_NOT_FOUND", f"数据源 {provider} 无配置")
    try:
        params = json.loads(r[1]) if r[1] else {}
    except (TypeError, ValueError):
        logger.warning("data_source_config(%s) params 非法 JSON，按空处理", provider)
        params = {}
    return r[0], params


def _save_ds_params(dsid: int, params: dict) -> None:
    """params 整体写回（读-改-写，单行 UPDATE 无并发窗口风险）。"""
    with get_conn() as conn:
        conn.execute("UPDATE data_source_config SET params=%s, updated_at=now() WHERE id=%s",
                     (json.dumps(params, ensure_ascii=False), dsid))
        conn.commit()


@router.get("/api/datasource/{provider}/points-presets")
def get_points_presets(provider: str,
                       payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """积分档预设表 + 当前档位 + 每 API 当前生效值（前端下拉/限速表格数据源）。"""
    cls = _preset_cls(provider)
    _, params = _load_ds_params(provider)
    tier = params.get("points_tier")
    tier_val = None
    try:
        tier_val = int(tier) if tier is not None else None
    except (TypeError, ValueError):
        pass   # 非法档位：回 None（= 未选档，老三级行为），四层解析里同样跳过
    preset = cls.POINTS_PRESETS.get(tier_val, {}) if tier_val is not None else {}
    overrides = params.get("rate_limits") or {}
    ds = cls(params=json.dumps(params))
    apis = []
    for name in sorted(set(cls.DEFAULT_RATE_LIMITS) | set(preset) | set(overrides)):
        apis.append({
            "api": name,
            "default": float(cls.DEFAULT_RATE_LIMITS.get(name, 0.0)),   # L0
            "preset": preset.get(name),      # L1 当前档预设值（未选档/键不在预设=None）
            "override": overrides.get(name),  # L2 显式覆写（None=未覆写）
            "effective": ds.get_rate_limit(name),   # 当前生效值（含 L3 时段乘数）
        })
    return {
        "provider": provider,
        "presets": {str(k): v for k, v in cls.POINTS_PRESETS.items()},   # JSON 键须字符串
        "current_tier": tier_val,
        "apis": apis,
        "circuit_breaker": {
            "fail_threshold": ds.get_param_float(
                "circuit_breaker", "fail_threshold", default=5.0, lo=1.0, hi=1000.0),
            "reset_timeout": ds.get_param_float(
                "circuit_breaker", "reset_timeout", default=60.0, lo=1.0, hi=86400.0),
        },
    }


@router.post("/api/datasource/{provider}/points-tier")
def set_points_tier(provider: str, req: PointsTierReq,
                    payload: dict = Depends(require_role("admin"))):
    """切换积分档（写 params.points_tier），返回逐 API 生效值 diff（旧档 vs 新档）。"""
    cls = _preset_cls(provider)
    if req.tier not in cls.POINTS_PRESETS:
        raise ApiError(400, "TIER_INVALID",
                       f"档位 {req.tier} 不在预设表（可选：{sorted(cls.POINTS_PRESETS)}）")
    dsid, params = _load_ds_params(provider)
    new_params = {**params, "points_tier": req.tier}
    old_ds = cls(params=json.dumps(params))
    new_ds = cls(params=json.dumps(new_params))
    names = set(cls.DEFAULT_RATE_LIMITS) | set(cls.POINTS_PRESETS[req.tier]) | set(params.get("rate_limits") or {})
    diff = [{"api": n, "before": old_ds.get_rate_limit(n), "after": new_ds.get_rate_limit(n)}
            for n in sorted(names) if old_ds.get_rate_limit(n) != new_ds.get_rate_limit(n)]
    _save_ds_params(dsid, new_params)
    audit_log(payload["username"], "data_source_points_tier", f"{provider} -> {req.tier}")
    return {"ok": True, "tier": req.tier, "diff": diff}


@router.post("/api/datasource/{provider}/rate-limit-override")
def set_rate_limit_override(provider: str, req: RateLimitOverrideReq,
                            payload: dict = Depends(require_role("admin"))):
    """单 API 限速覆写（L2）或熔断参数写入（params.circuit_breaker）。

    - {"api_name": "stk_mins", "value": 0.25}：覆写；value=null 删除覆写回落预设
    - {"circuit_breaker": {"fail_threshold": 8, "reset_timeout": 120}}：熔断参数（部分更新）
    后端范围校验：value ∈ [0, 86400]；fail_threshold ∈ [1,1000]；reset_timeout ∈ [1,86400]。
    """
    cls = _ds_cls(provider)
    dsid, params = _load_ds_params(provider)
    if req.circuit_breaker is not None:
        cb_in = req.circuit_breaker or {}
        try:
            ft = int(cb_in["fail_threshold"]) if cb_in.get("fail_threshold") is not None else None
            rt = float(cb_in["reset_timeout"]) if cb_in.get("reset_timeout") is not None else None
        except (TypeError, ValueError):
            raise ApiError(400, "CB_VALUE_INVALID", "熔断参数须为数字")
        if ft is not None and not 1 <= ft <= 1000:
            raise ApiError(400, "CB_VALUE_INVALID", "fail_threshold 须在 [1, 1000]")
        if rt is not None and not 1.0 <= rt <= 86400.0:
            raise ApiError(400, "CB_VALUE_INVALID", "reset_timeout 须在 [1, 86400] 秒")
        cb = dict(params.get("circuit_breaker") or {})
        if ft is not None:
            cb["fail_threshold"] = ft
        if rt is not None:
            cb["reset_timeout"] = rt
        params = {**params, "circuit_breaker": cb}
        _save_ds_params(dsid, params)
        audit_log(payload["username"], "data_source_cb_update", f"{provider} {cb}")
        return {"ok": True, "circuit_breaker": cb}
    if not req.api_name:
        raise ApiError(400, "OVERRIDE_VALUE_INVALID", "api_name 不能为空")
    overrides = dict(params.get("rate_limits") or {})
    if req.value is None:   # null = 删除覆写（回落预设）
        overrides.pop(req.api_name, None)
        action = "删除覆写"
    else:
        if not 0 <= req.value <= 86400:
            raise ApiError(400, "OVERRIDE_VALUE_INVALID", "覆写值须在 [0, 86400] 秒")
        overrides[req.api_name] = req.value
        action = f"覆写 {req.value}s"
    params = {**params, "rate_limits": overrides}
    _save_ds_params(dsid, params)
    ds = cls(params=json.dumps(params))
    audit_log(payload["username"], "data_source_rate_override", f"{provider} {req.api_name} {action}")
    return {"ok": True, "api": req.api_name, "value": req.value,
            "effective": ds.get_rate_limit(req.api_name)}


# --- 后台任务管理（PT1 平台化核心） ---


@router.get("/api/tasks")
def list_tasks_api(status: str | None = None, limit: int = 100,
                   payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.task_manager import list_tasks
    return {"items": list_tasks(status=status, limit=limit)}


@router.get("/api/tasks/{task_id}")
def get_task_api(task_id: str,
                 payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.task_manager import get_task
    t = get_task(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


@router.post("/api/tasks/{task_id}/terminate")
def terminate_task_api(task_id: str,
                       payload: dict = Depends(require_role("trader", "admin"))):
    from src.task_manager import terminate_task, log_task
    terminate_task(task_id)
    log_task(task_id, "WARN", f"用户 {payload['username']} 终止任务")
    audit_log(payload["username"], "task_terminate", task_id)
    return {"ok": True}


@router.post("/api/tasks/{task_id}/force-delete")
def force_delete_task_api(task_id: str,
                          payload: dict = Depends(require_role("admin"))):
    from src.task_manager import force_delete_task
    force_delete_task(task_id)
    audit_log(payload["username"], "task_force_delete", task_id)
    return {"ok": True}


@router.post("/api/tasks/detect-stuck")
def detect_stuck_api(payload: dict = Depends(require_role("admin"))):
    from src.task_manager import detect_stuck
    count = detect_stuck()
    return {"stuck_count": count}


# --- 消息通道管理（PT4 平台化消息层） ---


@router.get("/api/channels")
def list_channels(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, provider, name, credentials_encrypted IS NOT NULL, params, enabled, updated_at FROM channel_config ORDER BY provider")
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "has_credentials": bool(r[3]),
             "params": r[4], "enabled": r[5], "updated_at": str(r[6]) if r[6] else None} for r in rows]


@router.post("/api/channels")
def create_channel(req: ChannelReq, payload: dict = Depends(require_role("admin"))):
    from src.quant_common.crypto import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO channel_config (provider, name, credentials_encrypted, params, enabled) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (req.provider, req.name, enc, req.params, req.enabled))
        conn.commit()
    audit_log(payload["username"], "channel_create", req.provider)
    return {"id": cur.fetchone()[0]}


@router.post("/api/channels/{cid}")
def update_channel(cid: int, req: ChannelReq, payload: dict = Depends(require_role("admin"))):
    from src.quant_common.crypto import encrypt
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


@router.delete("/api/channels/{cid}")
def delete_channel(cid: int, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM channel_config WHERE id=%s", (cid,))
        conn.commit()
    audit_log(payload["username"], "channel_delete", f"id={cid}")
    return {"ok": True}


@router.post("/api/channels/{cid}/test")
def test_channel(cid: int, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.alert_notify.channel import _REGISTRY
    from src.quant_common.crypto import decrypt
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


@router.get("/api/brokers")
def list_brokers(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, provider, name, credentials_encrypted IS NOT NULL, params, enabled, updated_at FROM broker_config ORDER BY provider")
        rows = cur.fetchall()
    return [{"id": r[0], "provider": r[1], "name": r[2], "has_credentials": bool(r[3]),
             "params": r[4], "enabled": r[5], "updated_at": str(r[6]) if r[6] else None} for r in rows]


@router.post("/api/brokers")
def create_broker(req: BrokerReq, payload: dict = Depends(require_role("admin"))):
    from src.quant_common.crypto import encrypt
    enc = encrypt(req.credentials) if req.credentials else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO broker_config (provider, name, credentials_encrypted, params, enabled) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (req.provider, req.name, enc, req.params, req.enabled))
        conn.commit()
    audit_log(payload["username"], "broker_create", req.provider)
    return {"id": cur.fetchone()[0]}


@router.post("/api/brokers/{bid}")
def update_broker(bid: int, req: BrokerReq, payload: dict = Depends(require_role("admin"))):
    from src.quant_common.crypto import encrypt
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


@router.delete("/api/brokers/{bid}")
def delete_broker(bid: int, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM broker_config WHERE id=%s", (bid,))
        conn.commit()
    audit_log(payload["username"], "broker_delete", f"id={bid}")
    return {"ok": True}


@router.post("/api/brokers/{bid}/test")
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

