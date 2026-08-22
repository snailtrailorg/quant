"""平台化管理路由：数据源/通道/券商/任务/用量 —— 从 main.py 提取的 mgmt 端点。"""

from fastapi import APIRouter, Depends, Request, Body, HTTPException
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (DataSourceReq, ChannelReq, BrokerReq)
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

