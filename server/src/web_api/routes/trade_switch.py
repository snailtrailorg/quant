"""批 61·M6：账号切换操作面（A04 §九；半自动立法）。

端点（权限：nominate/confirm/extend/abort=strategy_control；execute=trade——analyst 不可达，
改实盘账号绑定的执行动作=admin/trader 专属【批 61 方案裁定 6】）：
GET  /api/trade-switch            会话列表（read；lazy expire 顺带）
GET  /api/trade-switch/{sid}      详情含 info_pack/checklist（read）
POST /api/trade-switch            提名（strategy_control；L1 预检=Broker 凭证完整性）
POST /api/trade-switch/{sid}/confirm /extend /abort（strategy_control）
POST /api/trade-switch/{sid}/execute（trade；含人工核实勾选+L1 复跑）

告警（safe_notify 通道：提名推送/超时升级/完成通知——飞书=通知通道非确认通道）；
L1 预检在路由层调 Broker.test_connection 传入引擎（分层：引擎零上行 import）。
"""
import json

from fastapi import APIRouter, Depends, Body

from ..auth import require_perm, audit_log
from ..errors import ApiError
from src.data_platform.db import get_conn
from src.data_platform import trade_switch as ts

router = APIRouter(tags=["trade-switch"])


def _l1_cred_ok(iid: int) -> bool:
    """L1 预检：Broker.test_connection=凭证字段完整性（纯本地检查；L2 真连挂账 Web 域不可达）。"""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT provider, credentials_encrypted, params FROM external_interface WHERE id=%s",
            (iid,)).fetchone()
    if not r:
        return False
    provider, cred_enc, params = r
    from src.strategy_framework.broker import _REGISTRY as _broker_reg
    cls = _broker_reg.get(provider)
    if not cls:
        return False
    params_str = json.dumps(params) if isinstance(params, dict) else params
    try:
        return bool(cls(credentials_encrypted=cred_enc, params=params_str).test_connection())
    except Exception:
        return False


def _notify(level: str, title: str, body: str = ""):
    """告警通知（fail-soft：通知失败不阻塞主流程）。"""
    try:
        from src.alert_notify.notify import safe_notify
        safe_notify(level, title, body)
    except Exception:
        pass


@router.get("/api/trade-switch")
def list_sessions(active_only: bool = False,
                  payload: dict = Depends(require_perm("read"))):
    out = ts.list_sessions(active_only=active_only)
    if out.get("expired_flipped"):
        _notify("warning", "账号切换会话已超时",
                f"{out['expired_flipped']} 个提名会话超过确认时限自动过期（旧链维持，未切换）")
    return out


@router.get("/api/trade-switch/{sid}")
def get_session(sid: int, payload: dict = Depends(require_perm("read"))):
    try:
        out = ts.get_session(sid)
    except LookupError as e:
        raise ApiError(404, "NOT_FOUND", str(e))
    if out.get("expired_flipped"):
        _notify("warning", "账号切换会话已超时",
                f"会话 #{sid} 超过确认时限自动过期（旧链维持，未切换）")
        audit_log(payload["username"], "trade_switch.expired", f"#{sid}", "详情读取时翻转")
    out.pop("expired_flipped", None)
    return out


@router.post("/api/trade-switch")
def nominate(body: dict = Body(...),
             payload: dict = Depends(require_perm("strategy_control"))):
    from_id, to_id = body.get("from_account"), body.get("to_account")
    # bool 是 int 子类（代码审 B-P3）：显式排除防 true 当 id=1
    if not isinstance(from_id, int) or isinstance(from_id, bool) \
            or not isinstance(to_id, int) or isinstance(to_id, bool):
        raise ApiError(400, "PARAM_INVALID", "from_account/to_account 须为整数账号 id")
    cred_ok = _l1_cred_ok(to_id)
    try:
        out = ts.nominate_switch(from_id, to_id, payload["username"], cred_ok)
    except ValueError as e:
        raise ApiError(400, "SWITCH_NOMINATE_REJECTED", str(e))
    except Exception as e:   # 唯一索引兜底并发（代码审 A-P1-1/B-P2-1：裸 UniqueViolation→500）
        if type(e).__name__ == "UniqueViolation":
            raise ApiError(409, "SWITCH_NOMINATE_DUPLICATE",
                           "目标账号已有活跃会话（并发提名冲突）")
        raise
    audit_log(payload["username"], "trade_switch.nominate",
              f"#{out['id']}", f"{from_id} → {to_id}（L1 凭证 OK）")
    return out


@router.post("/api/trade-switch/{sid}/confirm")
def confirm(sid: int, payload: dict = Depends(require_perm("strategy_control"))):
    try:
        out = ts.confirm_switch(sid, payload["username"])
    except LookupError as e:
        raise ApiError(404, "NOT_FOUND", str(e))
    except ValueError as e:
        raise ApiError(400, "SWITCH_STATE_INVALID", str(e))
    if out.get("state") == "expired":
        _notify("warning", "账号切换确认被拒（超时）", f"会话 #{sid} 确认时限已过已转 expired，旧链维持")
        audit_log(payload["username"], "trade_switch.confirm_expired", f"#{sid}")
    else:
        audit_log(payload["username"], "trade_switch.confirm", f"#{sid}")
    return out


@router.post("/api/trade-switch/{sid}/extend")
def extend(sid: int, payload: dict = Depends(require_perm("strategy_control"))):
    try:
        out = ts.extend_session(sid, payload["username"])
    except ValueError as e:
        raise ApiError(400, "SWITCH_EXTEND_REJECTED", str(e))
    audit_log(payload["username"], "trade_switch.extend", f"#{sid}")
    return out


@router.post("/api/trade-switch/{sid}/abort")
def abort(sid: int, body: dict = Body(default={}),
          payload: dict = Depends(require_perm("strategy_control"))):
    reason = str(body.get("reason", ""))[:200]
    try:
        out = ts.abort_switch(sid, payload["username"], reason)
    except ValueError as e:
        raise ApiError(400, "SWITCH_ABORT_REJECTED", str(e))
    audit_log(payload["username"], "trade_switch.abort", f"#{sid}", reason)
    return out


@router.post("/api/trade-switch/{sid}/execute")
def execute(sid: int, body: dict = Body(default={}),
            payload: dict = Depends(require_perm("trade"))):
    """执行切换（trade 键）：人工核实勾选 manual_verified 必传 + L1 凭证复跑（复评②实时性）。"""
    manual = body.get("manual_verified")
    if not isinstance(manual, bool):
        raise ApiError(400, "PARAM_INVALID", "manual_verified 须为布尔（在途人工核实勾选）")
    with get_conn() as conn:
        r = conn.execute("SELECT to_account FROM trade_switch_session WHERE id=%s",
                         (sid,)).fetchone()
    if not r:
        raise ApiError(404, "NOT_FOUND", f"会话 #{sid} 不存在")
    cred_ok = _l1_cred_ok(r[0])
    try:
        out = ts.execute_switch(sid, payload["username"], manual, cred_ok)
    except LookupError as e:
        raise ApiError(404, "NOT_FOUND", str(e))
    except ValueError as e:
        raise ApiError(400, "SWITCH_EXECUTE_REJECTED", str(e))
    if out.get("executed"):
        audit_log(payload["username"], "trade_switch.execute", f"#{sid}",
                  f"switched_tasks={out.get('switched_tasks')}")
        _notify("info", "账号切换完成",
                f"会话 #{sid}：{out.get('switched_tasks', 0)} 个任务已改绑新账号（任务不自动启停，"
                f"确认锚点 total_value={out.get('anchor', {}).get('total_value') if out.get('anchor') else 'N/A'}）")
    else:
        audit_log(payload["username"], "trade_switch.execute_blocked", f"#{sid}",
                  f"failed={','.join(out.get('recheck', {}).get('failed', []))}")
    return out
