"""批 62c：SM 对账路由 + 审计链血缘。

批 79（2026-09-29）删除 shadow 行情对账端点（shadow-diff/whitelist-stats/whitelist-verify/
shadow-check），保留 SM 差集与血缘链（signal→order_log 审计贯通，与对账无关）。
"""
from fastapi import APIRouter, Depends

from src.web_api.auth import require_perm
from src.web_api.errors import ApiError

router = APIRouter(prefix="/api/quality", tags=["quality"])


@router.get("/sm-reconcile")
async def sm_reconcile(_u: dict = Depends(require_perm("system_config"))):
    """SM 差集报告（62c——范围钉死 stock×L；ETF/转债范围外不计）。"""
    from src.data_platform.quality import sm_reconcile as _sr
    return _sr()


@router.get("/lineage/{signal_id}")
async def lineage(signal_id: int, _u: dict = Depends(require_perm("system_config"))):
    """审计链贯通（验收②）：signal→order_log.bar_fingerprint→血缘三列一条链（A03 §15.4）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT s.id, s.ts, s.strategy_id, s.symbol, s.action, s.source, s.fetched_at, s.dataset_version, "
            "o.bar_fingerprint FROM signal_log s "
            "LEFT JOIN order_log o ON o.signal_id = s.id "
            "WHERE s.id = %s ORDER BY o.id DESC LIMIT 1", (signal_id,))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "NOT_FOUND", "signal 不存在")
    return {"signal": dict(zip(("id", "ts", "strategy_id", "symbol", "action",
                                "source", "fetched_at", "dataset_version"), r[:8])),
            "bar_fingerprint": r[8]}
