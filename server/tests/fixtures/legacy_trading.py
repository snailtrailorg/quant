"""批 172b · 旧写库实现的逐字参照副本（仅等价测试引用，生产不留死代码）。

来源：`server/src/strategy_runner/trading.py`（2026-11-11 commit b306998 版本）。
用途：同一事件序列分别喂旧参照与新 domain 链，比对 SQL 参数/行。
"""
from __future__ import annotations

from src.data_platform.tz import as_utc
from src.strategy_runner.trading import ORDER_FINAL_STATES


def write_order_status(d, adapter, sid: str, symbol: str) -> None:
    try:
        from vnpy.trader.constant import Status
        m = {Status.PARTTRADED: "partial", Status.ALLTRADED: "all_traded",
             Status.CANCELLED: "cancelled", Status.REJECTED: "rejected"}
        new_status = m.get(getattr(d, "status", None))
        if not new_status:
            return
        from src.data_platform.db import get_conn
        vt = getattr(d, "vt_orderid", "") or ""
        with get_conn() as conn:
            row = None
            if vt:
                cur = conn.execute(
                    "SELECT id FROM order_log WHERE vt_orderid=%s ORDER BY id DESC LIMIT 1", (vt,))
                row = cur.fetchone()
            if not row:
                with adapter._lock:
                    cid = adapter._vt2cid.get(vt)
                if cid:
                    cur = conn.execute(
                        "SELECT id FROM order_log WHERE client_order_id=%s ORDER BY id DESC LIMIT 1", (cid,))
                    row = cur.fetchone()
            if not row:
                return
            conn.execute(
                "UPDATE order_log SET status=%s WHERE id=%s AND status <> ALL(%s)",
                (new_status, row[0], list(ORDER_FINAL_STATES)))
            conn.commit()
    except Exception:
        pass


def write_trade_log(d, adapter, sid: str, symbol: str) -> None:
    try:
        from vnpy.trader.constant import Direction

        from src.data_platform.db import get_conn
        action = "BUY" if d.direction == Direction.LONG else "SELL"
        vt = getattr(d, "vt_orderid", "")
        with adapter._lock:
            cid = adapter._vt2cid.get(vt)
        order_db_id, strategy_of = None, sid
        with get_conn() as conn:
            if cid:
                cur = conn.execute(
                    "SELECT id, strategy_id FROM order_log WHERE client_order_id=%s ORDER BY id DESC LIMIT 1",
                    (cid,))
                row = cur.fetchone()
                if row:
                    order_db_id, strategy_of = row[0], row[1] or sid
            elif vt:
                cur = conn.execute(
                    "SELECT id, strategy_id FROM order_log WHERE vt_orderid=%s ORDER BY id DESC LIMIT 1",
                    (vt,))
                row = cur.fetchone()
                if row:
                    order_db_id, strategy_of = row[0], row[1] or sid
            conn.execute(
                "INSERT INTO trade_log (ts, strategy_id, order_id, symbol, action, volume, price, trade_ref, account_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (trade_ref) DO NOTHING RETURNING id",
                (as_utc(getattr(d, "datetime", None)) if getattr(d, "datetime", None) is not None else None,
                 strategy_of, order_db_id, getattr(d, "symbol", symbol),
                 action, float(getattr(d, "volume", 0) or 0), float(getattr(d, "price", 0) or 0),
                 getattr(d, "vt_tradeid", None) or None,
                 getattr(adapter, "account_id", None)))
            conn.commit()
    except Exception:
        pass
