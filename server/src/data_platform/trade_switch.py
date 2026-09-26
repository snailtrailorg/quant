"""批 61·M6：交易账号切换状态机（A04 §九主规格；A03 §十全链）。

半自动立法：提名→人审确认→检查单实时复评→原子切换；不自动切换、不自动启停任务。

Web 域约束（批 61 方案双盲 B 两条 P0 的诚实化）：
- 预检 L1=凭证完整性（cred_ok 由路由层调 Broker.test_connection 传入——分层：本模块零上行
  import）+行校验；L2 真连+资金 Web 域不可达（无 TD 会话），挂账 worker/hub 会话探测另批。
- 在途判定两源：①DB 近似=当日 status IN (submitting,submitted) 且 NOT EXISTS trade_log
  关联成交（order_log 无终态回写——成交/撤单不落 status，已撤单保守误报=安全侧）
  ②确认者人工核实勾选（manual_verified，随 execute 提交落 checklist）。

超时语义（写死）：时限=确认截止 coalesce(extended_at, nominated_at)+timeout_s，仅 nominated
受辖（lazy expire+confirm 二次校验）；confirmed 无超时（仅 execute/abort 出边）。

检查单四必过（execute 时刻实时复评，A04 立法①——info_pack 仅人审快照）：
①在途清零（DB 近似=0 且 manual_verified）②L1 凭证+行校验 ③from 快照锚点存在（读不写，
checklist 记录最新行 ts+total_value）④affected_tasks 无 running（FOR UPDATE 锁内判定防 TOCTOU）。

分层：纯 DB 状态机+信息包聚合，零上行 import（test_layering 守门）；告警由路由层发。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from .db import get_conn

logger = logging.getLogger("data_platform.trade_switch")

ACTIVE_STATES = ("nominated", "confirmed")
ALL_STATES = ("nominated", "confirmed", "done", "expired", "aborted")

# 近似在途：submitting/submitted 且当日（上海时区）且无关联成交行。
# 关联键=t.order_id = o.id（code 审双盲 A/B 同判 P0 修正：trade_log 无 client_order_id 列；
# order_id 存 order_log.id（trading.py:90 write_trade_log 先例），bigint 对齐非 vt_orderid）。
# order_id 可 NULL 漏关联+部分成交整单被排除+撤单无 DB 痕迹永留 submitted——三口径均保守/
# 放行欠报方向并存，靠确认者人工勾选兜底（根治=order_log 终态回写，挂账另批）。
_OPEN_SQL = """
SELECT o.client_order_id, o.symbol, o.status, o.ts
FROM order_log o
WHERE o.account_id = %s
  AND o.status IN ('submitting','submitted')
  AND (o.ts AT TIME ZONE 'Asia/Shanghai')::date = (now() AT TIME ZONE 'Asia/Shanghai')::date
  AND NOT EXISTS (
      SELECT 1 FROM trade_log t WHERE t.account_id = o.account_id
        AND t.order_id = o.id)
ORDER BY o.ts
"""


def _timeout_s(conn) -> int:
    """确认时限（秒）：routing_policy live 行，无行 fallback default（0093 DEFAULT 300）。

    A03 §10.3 的 120s 为蓝图期数字，以 0093/A04 的 300s 为准（批 61 方案注记）。
    """
    row = conn.execute(
        "SELECT trade_switch_confirm_timeout_s FROM routing_policy WHERE consumer_tag='live'"
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT trade_switch_confirm_timeout_s FROM routing_policy WHERE consumer_tag='default'"
        ).fetchone()
    if not row or not row[0]:
        return 300
    return max(30, int(row[0]))


def _fetch_account(conn, aid: int) -> dict | None:
    cur = conn.execute(
        "SELECT id, name, provider, market, capabilities, enabled FROM external_interface WHERE id=%s",
        (aid,))
    r = cur.fetchone()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "provider": r[2], "market": r[3],
            "capabilities": list(r[4] or []), "enabled": r[5]}


def _db_open_orders(conn, account_id: int) -> list[dict]:
    """近似在途（当日无成交关联）。unfilled=其中有任何成交行者之外——本口径下二者同集
    （有成交即被 NOT EXISTS 排除）；区分保留在 info_pack 字段语义里（A04 五字段形状忠实）。
    ts ISO 序列化（psycopg 返回 datetime——info_pack jsonb 序列化需要）。"""
    cur = conn.execute(_OPEN_SQL, (account_id,))
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        if isinstance(d.get("ts"), datetime):
            d["ts"] = d["ts"].isoformat()
        out.append(d)
    return out


def _to_account_state(conn, to_id: int) -> dict:
    """to 账号状态：最近 account_snapshot 行+时刻；无行明示（Web 域无 TD 会话不可即时查询——
    L2 挂账；to 通常无运行任务故无快照属常态，禁止伪装成「连通 OK」）。"""
    r = conn.execute(
        "SELECT ts, total_value, daily_pnl, available_cash FROM account_snapshot "
        "WHERE account_id=%s ORDER BY ts DESC LIMIT 1", (to_id,)).fetchone()
    if not r:
        return {"available": False,
                "note": "无数据（该账号无运行任务，Web 域不可即时查询——真连预检挂账）"}
    return {"available": True, "snapshot_ts": r[0].isoformat() if r[0] else None,
            "total_value": float(r[1]) if r[1] is not None else None,
            "daily_pnl": float(r[2]) if r[2] is not None else None,
            "available_cash": float(r[3]) if r[3] is not None else None}


def _perms_reeval(conn, to: dict) -> dict:
    """market_op 五键重估（to 市场维度标注——确认者须持适用键）。"""
    from src.quant_common.markets import MARKET_OP_DECOMP
    op_key = next((k for k, (m, _c, _e) in MARKET_OP_DECOMP.items() if m == to["market"]), None)
    keys = {}
    for k, (m, _c, _e) in MARKET_OP_DECOMP.items():
        keys[k] = {"market": m, "applies_to_to": m == to["market"]}
    return {"to_market": to["market"], "op_key": op_key, "keys": keys}


def _affected_tasks(conn, from_id: int) -> list[dict]:
    cur = conn.execute(
        "SELECT id, name, status FROM live_task WHERE account_id=%s ORDER BY id", (from_id,))
    return [{"id": r[0], "name": r[1], "status": r[2]} for r in cur.fetchall()]


def _checklist_l1(to: dict, cred_ok: bool) -> dict:
    """L1 检查快照（提名时刻落库；execute 时实时复评更新）。"""
    return {
        "cred_complete": bool(cred_ok),
        "to_enabled": bool(to["enabled"]),
        "to_has_trading": "trading" in to["capabilities"],
        "same_market": True,   # nominate 已校验，execute 复评重算
        "l2_live_probe": "挂账（Web 域无 TD 会话）",
    }


def _row_dict(cur, r, timeout_s: int) -> dict:
    cols = [d[0] for d in cur.description] + ["_timeout_s"]
    return dict(zip(cols, tuple(r) + (timeout_s,)))


def _lazy_expire(conn) -> int:
    """过期翻转（仅 nominated；单行只转一次=expired 终态幂等）。返回翻转数，路由层据此发告警。"""
    cur = conn.execute(
        "UPDATE trade_switch_session SET state='expired' "
        "WHERE state='nominated' AND "
        "  coalesce(extended_at, nominated_at) + make_interval(secs => %s) < now() "
        "RETURNING id, to_account", (_timeout_s(conn),))
    rows = cur.fetchall()
    if rows:
        conn.commit()
    return len(rows)


def nominate_switch(from_id: int, to_id: int, actor: str, cred_ok: bool) -> dict:
    """提名：行校验+L1 预检+信息包聚合+INSERT nominated（唯一索引兜底 to 侧并发）。

    L1 凭证不完整即拒（A04 立法②「死账号挡在提名前」；代码审 A-P1-4/B-P1-2 同判裁定）。
    """
    if from_id == to_id:
        raise ValueError("from/to 不能相同")
    with get_conn() as conn:
        frm, to = _fetch_account(conn, from_id), _fetch_account(conn, to_id)
        if not frm or not to:
            raise ValueError("账号不存在")
        if not cred_ok:
            raise ValueError("目标账号 L1 凭证不完整（连接测试未过）——死账号挡在提名前")
        if frm["market"] != to["market"]:
            raise ValueError(f"跨市场切换无意义（{frm['market']} → {to['market']}）")
        if not to["enabled"]:
            raise ValueError("目标账号未启用")
        if "trading" not in to["capabilities"]:
            raise ValueError("目标账号无 trading 能力")
        active = conn.execute(
            "SELECT id FROM trade_switch_session WHERE to_account=%s AND state IN ('nominated','confirmed')",
            (to_id,)).fetchone()
        if active:
            raise ValueError(f"目标账号已有活跃会话 #{active[0]}")
        open_orders = _db_open_orders(conn, from_id)
        info_pack = {
            "open_orders": open_orders,
            "unfilled": open_orders,   # 同集（成交即被排除）；字段形状忠实 A04 五字段
            "open_orders_caveat": "DB 近似口径：已撤单无 DB 痕迹会残留（保守）；执行前须人工核实勾选",
            "to_account_state": _to_account_state(conn, to_id),
            "perms_reeval": _perms_reeval(conn, to),
            "affected_tasks": _affected_tasks(conn, from_id),
        }
        checklist = _checklist_l1(to, cred_ok)
        checklist["manual_verified"] = False
        cur = conn.execute(
            "INSERT INTO trade_switch_session (state, from_account, to_account, info_pack, checklist) "
            "VALUES ('nominated', %s, %s, %s::jsonb, %s::jsonb) RETURNING id, nominated_at",
            (from_id, to_id, json.dumps(info_pack), json.dumps(checklist)))
        r = cur.fetchone()
        conn.commit()
        timeout_s = _timeout_s(conn)
        return {"id": r[0], "nominated_at": r[1].isoformat(), "timeout_s": timeout_s,
                "checklist": checklist}


def list_sessions(active_only: bool = False) -> dict:
    """会话列表（lazy expire 顺带；返回 timeout_s 供前端倒计时）。"""
    with get_conn() as conn:
        expired_n = _lazy_expire(conn)
        cond = "WHERE state IN ('nominated','confirmed')" if active_only else ""
        timeout_s = _timeout_s(conn)
        cur = conn.execute(
            f"SELECT id, state, from_account, to_account, nominated_at, confirmed_at, done_at, extended_at "
            f"FROM trade_switch_session {cond} ORDER BY id DESC LIMIT 200")
        items = []
        for r in cur.fetchall():
            d = dict(zip([x[0] for x in cur.description], r))
            d["nominated_at"] = d["nominated_at"].isoformat() if d["nominated_at"] else None
            for k in ("confirmed_at", "done_at", "extended_at"):
                d[k] = d[k].isoformat() if d[k] else None
            d["timeout_s"] = timeout_s
            items.append(d)
        return {"items": items, "expired_flipped": expired_n, "timeout_s": timeout_s}


def get_session(sid: int) -> dict:
    with get_conn() as conn:
        flipped = _lazy_expire(conn)
        timeout_s = _timeout_s(conn)
        cur = conn.execute(
            "SELECT id, state, from_account, to_account, info_pack, checklist, "
            "nominated_at, confirmed_at, done_at, extended_at "
            "FROM trade_switch_session WHERE id=%s", (sid,))
        r = cur.fetchone()
        if not r:
            raise LookupError(f"会话 #{sid} 不存在")
        d = _row_dict(cur, r, timeout_s)
        for k in ("nominated_at", "confirmed_at", "done_at", "extended_at"):
            d[k] = d[k].isoformat() if d[k] else None
        d.pop("_timeout_s")
        d["timeout_s"] = timeout_s
        d["expired_flipped"] = flipped   # 详情路径 lazy 翻转计数（路由层据此告警）
        return d


def confirm_switch(sid: int, actor: str) -> dict:
    """nominated→confirmed。时限判断走 DB 时钟（代码审 A-P2-4：与 lazy expire 同源，
    消 web/DB 钟差双源不一致）；SQL 时限谓词原子判定，0 行时归因。"""
    with get_conn() as conn:
        timeout_s = _timeout_s(conn)
        cur = conn.execute(
            "UPDATE trade_switch_session SET state='confirmed', confirmed_at=now() "
            "WHERE id=%s AND state='nominated' AND "
            "  coalesce(extended_at, nominated_at) + make_interval(secs => %s) >= now() "
            "RETURNING id", (sid, timeout_s))
        if cur.fetchone():
            conn.commit()
            return {"id": sid, "state": "confirmed"}
        # 0 行归因：超时（nominated+过限→expired）或状态非 nominated
        row = conn.execute(
            "SELECT state FROM trade_switch_session WHERE id=%s", (sid,)).fetchone()
        if not row:
            raise LookupError(f"会话 #{sid} 不存在")
        if row[0] == "nominated":
            # 状态守卫防窄窗竞态（快审 P3：SELECT 与 UPDATE 非原子——刚续时的会话不误翻）
            conn.execute("UPDATE trade_switch_session SET state='expired' "
                         "WHERE id=%s AND state='nominated'", (sid,))
            conn.commit()
            return {"id": sid, "state": "expired", "reason": "确认时限已过"}
        raise ValueError(f"状态 {row[0]} 不可确认（须 nominated）")


def extend_session(sid: int, actor: str) -> dict:
    """续时一次（nominated 且未续过且**时限未过**——代码审 A-P2-5：防复活已超时提名）。"""
    with get_conn() as conn:
        timeout_s = _timeout_s(conn)
        cur = conn.execute(
            "UPDATE trade_switch_session SET extended_at=now() "
            "WHERE id=%s AND state='nominated' AND extended_at IS NULL AND "
            "  coalesce(extended_at, nominated_at) + make_interval(secs => %s) >= now() "
            "RETURNING id", (sid, timeout_s))
        if not cur.fetchone():
            raise ValueError("续时失败（须 nominated、未续过且未超时）")
        conn.commit()
        return {"id": sid, "state": "nominated", "extended": True, "timeout_s": timeout_s}


def abort_switch(sid: int, actor: str, reason: str) -> dict:
    """nominated/confirmed→aborted（人工取消，带理由落 checklist）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE trade_switch_session SET state='aborted', "
            "checklist = checklist || %s::jsonb "
            "WHERE id=%s AND state IN ('nominated','confirmed') RETURNING id, state",
            (json.dumps({"aborted_by": actor, "abort_reason": reason[:200]}), sid))
        r = cur.fetchone()
        if not r:
            raise ValueError("中止失败（会话不存在或已终态）")
        conn.commit()
        return {"id": r[0], "state": "aborted"}


def _recheck_locked(conn, from_id: int, to_id: int, manual_verified: bool, cred_ok: bool) -> dict:
    """检查单五必过（live_task 行已锁——execute 事务内调用）。

    第五项=from 仍被 live_task 引用（代码审 A-P1-2：唯一索引只挡 to 侧，链式会话
    A:1→2 与 B:1→3 并存时后执行者 UPDATE 0 行=假成功零效果）。
    """
    frm = _fetch_account(conn, from_id)
    to = _fetch_account(conn, to_id)
    detail: dict = {}
    # ① 在途清零（两源：DB 近似 + 人工勾选）
    open_orders = _db_open_orders(conn, from_id)
    detail["open_orders_count"] = len(open_orders)
    detail["manual_verified"] = bool(manual_verified)
    ok1 = len(open_orders) == 0 and bool(manual_verified)
    # ② L1：行校验（enabled/trading/同 market——代码审 A-P1-3 补）+ 凭证完整性
    ok2 = bool(to and frm and to["enabled"] and "trading" in to["capabilities"]
               and to["market"] == frm["market"] and cred_ok)
    detail["to_enabled_trading"] = bool(to and to["enabled"] and "trading" in to["capabilities"])
    detail["same_market"] = bool(frm and to and frm["market"] == to["market"])
    detail["cred_complete"] = bool(cred_ok)
    # ③ from 快照锚点（读不写——记录 ts+total_value 为对账锚点值）
    r = conn.execute(
        "SELECT ts, total_value FROM account_snapshot WHERE account_id=%s ORDER BY ts DESC LIMIT 1",
        (from_id,)).fetchone()
    detail["anchor_snapshot"] = ({"ts": r[0].isoformat(), "total_value": float(r[1])}
                                 if r else None)
    ok3 = r is not None
    # ④ 无 running（锁内判定——TOCTOU 收口）
    run_n = conn.execute(
        "SELECT count(*) FROM live_task WHERE account_id=%s AND status='running'",
        (from_id,)).fetchone()[0]
    detail["running_tasks"] = run_n
    ok4 = run_n == 0
    # ⑤ from 仍被引用（锁内计数——0=会话已空切）
    ref_n = conn.execute(
        "SELECT count(*) FROM live_task WHERE account_id=%s", (from_id,)).fetchone()[0]
    detail["referenced_tasks"] = ref_n
    ok5 = ref_n > 0
    detail["pass"] = bool(ok1 and ok2 and ok3 and ok4 and ok5)
    detail["failed"] = [name for name, ok in
                        (("open_orders_clear", ok1), ("to_account_l1", ok2),
                         ("anchor_snapshot", ok3), ("no_running_tasks", ok4),
                         ("from_still_referenced", ok5)) if not ok]
    return detail


def execute_switch(sid: int, actor: str, manual_verified: bool, cred_ok: bool) -> dict:
    """confirmed→四必过复评→单事务原子切换→done（TOCTOU 锁内判定）。

    复评不过：checklist 更新独立提交（复评结果留痕），confirmed 保持，切换零副作用；
    过：UPDATE live_task（affected 全行 account_id→to）+session→done 同事务提交。
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, state, from_account, to_account FROM trade_switch_session "
            "WHERE id=%s FOR UPDATE", (sid,))
        r = cur.fetchone()
        if not r:
            raise LookupError(f"会话 #{sid} 不存在")
        if r[1] != "confirmed":
            raise ValueError(f"状态 {r[1]} 不可执行（须 confirmed）")
        from_id, to_id = r[2], r[3]
        # TOCTOU 收口：先锁 affected live_task 行再判定（④ 在锁内）
        conn.execute("SELECT id FROM live_task WHERE account_id=%s FOR UPDATE", (from_id,))
        detail = _recheck_locked(conn, from_id, to_id, manual_verified, cred_ok)
        if not detail["pass"]:
            conn.execute(
                "UPDATE trade_switch_session SET checklist=checklist || %s::jsonb WHERE id=%s",
                (json.dumps({"last_recheck": detail, "recheck_by": actor}), sid))
            conn.commit()
            return {"id": sid, "state": "confirmed", "executed": False, "recheck": detail}
        n = conn.execute(
            "UPDATE live_task SET account_id=%s, updated_at=now() WHERE account_id=%s",
            (to_id, from_id)).rowcount
        conn.execute(
            "UPDATE trade_switch_session SET state='done', done_at=now(), "
            "checklist=checklist || %s::jsonb WHERE id=%s",
            (json.dumps({"last_recheck": detail, "recheck_by": actor,
                         "switched_tasks": n}), sid))
        conn.commit()
        return {"id": sid, "state": "done", "executed": True, "switched_tasks": n,
                "anchor": detail["anchor_snapshot"]}
