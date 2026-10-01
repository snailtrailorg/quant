"""批 76 F1/F2：冻结事件事实表读写 + 解冻握手（token/请求键）单点。

**分层**：本模块在层 1（`data_platform`，拥有 DB 与 Valkey 的层）——上游消费者
`strategy_runner.hub_worker`（写冻结/解冻事实）与 `web_api.routes.trading`（人工解冻面）
都向下依赖它，无反向边。

**两条通道（分工固定，勿混）**：
- PG `freeze_event` = **事实**（冻过几次、谁解的、用哪种方式）——查询面/审计用；
- Valkey = **控制**（瞬时命令与握手）：
  - `hub:unfreeze:{tid}`：Web/IM 写下的人工解冻请求 → worker 5s 钩子 GETDEL 消费；
  - `im:unfreeze_token:{token}`：IM 发起解冻时的一次性确认 token（Web 确认链）；
    `consume` 用 GETDEL 保证**一次性**（重放/并发双击只有一个赢家）。

**校验纪律**：
- 事实表写失败**不得**阻断冻结本身（调用方 fail-soft——冻结优先于记账）；
- 解冻请求键必须带 `operator` 与 `channel`（人工解冻不留名=审计缺口）；
- token 载荷必须带 `tid`（Web 侧须与路径 tid 一致，防「A 任务的链接解冻 B 任务」）。
"""
from __future__ import annotations

import json
import logging
import secrets
import time

from src.data_platform.db import get_conn
from src.data_platform.jsonb import jsonb

logger = logging.getLogger("data_platform.freeze_event")

FREEZE_TYPES = ("ts_gap", "seq_gap", "untrusted")
UNFREEZE_METHODS = ("restart", "auto_reconnect", "manual_web", "manual_im")

TOKEN_TTL_S = 600            # 一次性确认 token 存活（IM 发起→Web 确认的合理窗口）
UNFREEZE_REQ_TTL_S = 300     # 解冻请求键存活兜底（worker 消费即删；过期=停摆后不留僵尸令）
TOKEN_KEY = "im:unfreeze_token:{token}"
UNFREEZE_REQ_KEY = "hub:unfreeze:{tid}"


# ——— 事实表（F1） ———

def record_freeze(task_id: int, symbol: str, freeze_type: str, *, account_id: int | None = None,
                  watermark: str | None = None, gap_target_ts: str | None = None,
                  detail: dict | None = None) -> int | None:
    """记一次冻结（返回事件 id；失败抛——调用方 fail-soft 包裹，冻结优先于记账）。"""
    if freeze_type not in FREEZE_TYPES:
        raise ValueError(f"未知 freeze_type={freeze_type!r}（合法={FREEZE_TYPES}）")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO freeze_event (task_id, account_id, symbol, freeze_type, watermark, "
            "gap_target_ts, detail) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (task_id, account_id, symbol, freeze_type, watermark, gap_target_ts,
             jsonb(detail) if detail is not None else None))
        event_id = cur.fetchone()[0]
        conn.commit()
    return event_id


def close_freeze(task_id: int, *, method: str, operator: str | None = None,
                 symbol: str | None = None) -> int:
    """关闭该任务（可再按 symbol 收窄）所有进行中事件；返回关闭行数。

    幂等：无进行中事件时返回 0（人工解冻对「其实没冻」的任务=空操作，不报错）。
    """
    if method not in UNFREEZE_METHODS:
        raise ValueError(f"未知解冻方式={method!r}（合法={UNFREEZE_METHODS}）")
    sql = ("UPDATE freeze_event SET unfrozen_at=now(), unfreeze_method=%s, operator=%s "
           "WHERE task_id=%s AND unfrozen_at IS NULL")
    args: list = [method, operator, task_id]
    if symbol is not None:
        sql += " AND symbol=%s"
        args.append(symbol)
    with get_conn() as conn:
        cur = conn.execute(sql, tuple(args))
        n = cur.rowcount
        conn.commit()
    return n


def list_events(task_id: int | None = None, limit: int = 50) -> list[dict]:
    """事件查询面（任务页/运维/测试）：默认全量最近 limit 条，可按 task 过滤。"""
    sql = ("SELECT id, task_id, account_id, symbol, freeze_type, frozen_at, watermark, "
           "gap_target_ts, unfrozen_at, unfreeze_method, operator FROM freeze_event")
    args: list = []
    if task_id is not None:
        sql += " WHERE task_id=%s"
        args.append(task_id)
    sql += " ORDER BY id DESC LIMIT %s"
    args.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [{"id": r[0], "task_id": r[1], "account_id": r[2], "symbol": r[3], "freeze_type": r[4],
             "frozen_at": str(r[5]) if r[5] else None, "watermark": r[6], "gap_target_ts": r[7],
             "unfrozen_at": str(r[8]) if r[8] else None, "unfreeze_method": r[9],
             "operator": r[10]} for r in rows]


# ——— 控制面（F2）：解冻请求键 + 一次性确认 token ———

def _valkey():
    from src.quant_common.redis_client import business_redis
    return business_redis(decode_responses=True, socket_timeout=2, socket_connect_timeout=2)


def request_unfreeze(tid: int, *, operator: str, channel: str, note: str | None = None) -> dict:
    """写人工解冻请求键（worker 5s 钩子 GETDEL 消费）。channel='manual_web'|'manual_im'。"""
    if channel not in ("manual_web", "manual_im"):
        raise ValueError(f"未知解冻通道={channel!r}")
    payload = {"operator": operator, "channel": channel, "ts": int(time.time())}
    if note:
        payload["note"] = note
    _valkey().set(UNFREEZE_REQ_KEY.format(tid=int(tid)), json.dumps(payload, ensure_ascii=False),
                  ex=UNFREEZE_REQ_TTL_S)
    return payload


def make_confirm_token(tid: int, *, operator: str, open_id: str = "", symbol: str = "",
                       freeze_type: str = "") -> str:
    """IM 发起解冻 → 生成一次性确认 token（回执给管理员的是 Web 深链，非密码）。"""
    token = secrets.token_urlsafe(24)
    payload = {"tid": int(tid), "operator": operator, "open_id": open_id, "symbol": symbol,
               "freeze_type": freeze_type, "created_at": int(time.time())}
    _valkey().set(TOKEN_KEY.format(token=token), json.dumps(payload, ensure_ascii=False),
                  ex=TOKEN_TTL_S)
    return token


def peek_confirm_token(token: str) -> dict | None:
    """只读预览（Web 确认页展示「要解冻哪个任务」用）；无效/过期返回 None。"""
    if not token:
        return None
    raw = _valkey().get(TOKEN_KEY.format(token=token))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def consume_confirm_token(token: str) -> dict | None:
    """一次性消费（GETDEL）：返回载荷；已被用过/过期返回 None（重放拒绝）。"""
    if not token:
        return None
    r = _valkey()
    try:
        raw = r.getdel(TOKEN_KEY.format(token=token))   # redis-py ≥4/Valkey：原子取删
    except AttributeError:                              # 老驱动回退（非原子——测试 fake 兼容）
        raw = r.get(TOKEN_KEY.format(token=token))
        if raw:
            r.delete(TOKEN_KEY.format(token=token))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def web_base_url() -> str:
    """Web 外部基址（system_config 键 web_base_url，迁移 0123 种子）；未配置返回 ''。"""
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM system_config WHERE key='web_base_url'").fetchone()
    except Exception as e:   # 读失败=未配置（IM 回执降级提示，不阻断）
        logger.warning("web_base_url 读失败（按未配置处理）: %s", e)
        return ""
    return (row[0] or "").strip().rstrip("/") if row else ""
