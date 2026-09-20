"""批 57·M2：路由策略操作面（29 号 §五"表-码-面"之面——weights 编辑+候选链试算器）。

GET /api/routing/policies     权重行列表（read）
PATCH /api/routing/policies/{tag}  weights/bulkhead 编辑（system_config）——保存后 bump cfg:version
GET /api/routing/dry-run      候选链试算器（read）：kind/symbol/consumer/mode → 链+决策理由
GET /api/routing/decisions    审计最近行（read）
"""
from fastapi import APIRouter, Depends, Body
from ..auth import require_perm, audit_log
from ..errors import ApiError
from src.data_platform.db import get_conn

router = APIRouter(tags=["routing"])


@router.get("/api/routing/policies")
def list_policies(payload: dict = Depends(require_perm("system_config"))):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT consumer_tag, weights, bulkhead_defaults, trade_switch_confirm_timeout_s "
            "FROM routing_policy ORDER BY consumer_tag")
        cols = [d[0] for d in cur.description]
        return {"items": [dict(zip(cols, r)) for r in cur.fetchall()]}


@router.patch("/api/routing/policies/{consumer_tag}")
def update_policy(consumer_tag: str, body: dict = Body(...),
                  payload: dict = Depends(require_perm("system_config"))):
    """weights 三键必含（CHECK 锁）；bulkhead_defaults 可选。保存=bump cfg:version（各进程 2s 内对账生效）。"""
    import json
    w = body.get("weights")
    if not isinstance(w, dict) or not {"completeness", "cost", "latency"} <= set(w):
        raise ApiError(400, "PARAM_INVALID", "weights 须含 completeness/cost/latency 三键")
    try:
        for v in w.values():
            float(v)
    except (TypeError, ValueError):
        raise ApiError(400, "PARAM_INVALID", "weights 数值须为数字")
    bh = body.get("bulkhead_defaults")
    if bh is not None and (not isinstance(bh, dict)
                           or not all(isinstance(v, int) and v >= 1 for v in bh.values())):
        raise ApiError(400, "PARAM_INVALID", "bulkhead_defaults 须为 {数据源: 正整数} 对象")
    with get_conn() as conn:
        # 部分更新语义（盲审 A/B P1）：body 无 bulkhead_defaults 键=该列不动（防保存权重清空覆写）
        if bh is None:
            cur = conn.execute(
                "UPDATE routing_policy SET weights=%s::jsonb "
                "WHERE consumer_tag=%s RETURNING consumer_tag", (json.dumps(w), consumer_tag))
        else:
            cur = conn.execute(
                "UPDATE routing_policy SET weights=%s::jsonb, bulkhead_defaults=%s::jsonb "
                "WHERE consumer_tag=%s RETURNING consumer_tag",
                (json.dumps(w), json.dumps(bh), consumer_tag))
        if not cur.fetchone():
            raise ApiError(404, "NOT_FOUND", f"consumer_tag {consumer_tag} 不存在")
        conn.commit()
    from src.data_platform.routing import bump_config_version
    epoch = bump_config_version()
    audit_log(payload["username"], "routing_policy.update", f"{consumer_tag} epoch={epoch}")
    return {"ok": True, "epoch": epoch}


@router.get("/api/routing/dry-run")
def dry_run(kind: str, symbol: str, consumer: str = "default", mode: str = "consume",
            payload: dict = Depends(require_perm("system_config"))):
    """候选链试算器：输入 kind/symbol/consumer/mode 展示链与决策理由（不真拉——29 号只选不拉）。"""
    from src.quant_common.contract import DataRequest
    from src.data_platform import routing
    from src.data_platform.rate_limit import _BREAKERS
    req = DataRequest(kind=kind, symbols=(symbol,), temporality="historical",
                      consumer_tag=consumer, mode=mode)
    chain = routing.resolve(req)
    out = []
    for c in chain.candidates:
        breaker = _BREAKERS.get(c.adapter)
        out.append({
            "adapter": c.adapter, "is_local": c.is_local, "position": c.position,
            "health": getattr(breaker, "state", "closed"),
            "quality": c.quality,
            "score": routing._score(routing._weights_of(consumer), c.quality),
        })
    return {"epoch": chain.epoch, "weights": routing._weights_of(consumer),
            "chain": out, "local_kinds": sorted(routing.LOCAL_KINDS)}


@router.get("/api/routing/decisions")
def list_decisions(limit: int = 50, payload: dict = Depends(require_perm("system_config"))):
    """审计最近行（req_summary 可读——四审架构建议）。"""
    limit = max(1, min(limit, 200))
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, ts, fingerprint, req_summary, chain, epoch, cause "
            "FROM routing_decision ORDER BY id DESC LIMIT %s", (limit,))
        cols = [d[0] for d in cur.description]
        return {"items": [dict(zip(cols, r)) for r in cur.fetchall()]}
