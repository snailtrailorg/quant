"""纸上交易数据访问（层 1）——批 86-B 路由层 SQL 下沉（no_sql 闸门立法的直接产物）。

`tests/test_no_sql_in_new_routes.py` 冻结了 routes/*.py 的 SQL 计数（**只许调低不许调高**），
批 86-B 给 trading.py 新增的 6 处内联 SQL 按批 83a 先例（mgmt.py → config_store.py）
全量下沉本模块。路由层保留：权限闸、mode 分档、参数校验、审计、心跳合并（redis 非 SQL）。

⚠️ **get_conn 必须函数内 import**（`from src.data_platform.db import get_conn`），
不得模块顶 import 后闭包持有：测试层用 `patch("src.data_platform.db.get_conn")`
打桩（perms.py/auth.py 同法）——模块顶 `from X import Y` 会在 import 时拷贝引用，
patch 替换 db 模块属性后闭包里的旧引用打不进去（假直连真库）。
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def list_paper_task_rows(status: str | None = None) -> list[tuple]:
    """列纸上任务原始行（mode='paper'）——`GET /api/paper-trade` 的数据面。

    返回列序：id, name, strategy_id, symbol, params, status,
    account_id, initial_capital, created_at, account_name（JOIN trading_account 取名）。
    """
    from src.data_platform.db import get_conn
    _base = ("SELECT lt.id, lt.name, lt.strategy_id, lt.symbol, lt.params, lt.status, "
             "lt.account_id, lt.initial_capital, lt.created_at, ta.name "
             "FROM live_task lt LEFT JOIN trading_account ta ON ta.id=lt.account_id "
             "WHERE lt.mode='paper' ")
    with get_conn() as conn:
        if status:
            cur = conn.execute(_base + "AND lt.status=%s ORDER BY lt.id DESC", (status,))
        else:
            cur = conn.execute(_base + "ORDER BY lt.id DESC")
        return cur.fetchall()


def get_virtual_account_id() -> int | None:
    """系统虚拟账户 id（`trading_account.is_virtual=true`，0127 建）。

    None=虚拟账户不存在（0127 未执行）——调用方须 500 显式报错，**不得**静默
    把 paper 任务绑到真实账户（预算隔离的最后一道保险，调用方忽略 None 即穿）。
    """
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM trading_account WHERE is_virtual = true LIMIT 1").fetchone()
    return row[0] if row else None


def get_task_snapshot_params(tid: int) -> tuple | None:
    """任务的（策略快照, 现参数）原始值——PUT 编辑时按快照里的 parameter_defs 校验。

    快照是真源（策略可能在建任务后被改过）；None=任务不存在。
    返回 (strategy_snapshot_raw, params_raw)，json 解析留给调用方（职责分离）。
    """
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        return conn.execute(
            "SELECT strategy_snapshot, params FROM live_task WHERE id=%s", (tid,)).fetchone()


def update_task_fields(tid: int, fields: dict) -> bool:
    """按**白名单键**更新任务可编辑字段（name/initial_capital/params）。

    ⚠️ 值必须已由调用方校验/定型（name 非空、initial_capital float、params 已
    按快照 defs 校验并 json.dumps）——本函数只做 SQL 组装，不做业务校验。
    白名单外键**静默丢弃**不可取 ⇒ 调用方传未知键时抛 ValueError（防上游拼错键
    导致「以为改了其实没改」的静默失败——本仓在录缺陷类）。
    恒刷 updated_at。返回是否有行被更新（False=任务不存在）。
    """
    allowed = ("name", "initial_capital", "params")
    unknown = [k for k in fields if k not in allowed]
    if unknown:
        raise ValueError(f"update_task_fields 白名单外字段: {unknown}（调用方组装错误）")
    if not fields:
        return False
    from src.data_platform.db import get_conn
    sets = [f"{k}=%s" for k in fields]
    sets.append("updated_at=now()")
    args = list(fields.values()) + [tid]
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE live_task SET {','.join(sets)} WHERE id=%s", tuple(args))
        conn.commit()
        return (cur.rowcount or 0) > 0


def get_task_equity_source(tid: int, limit: int) -> tuple | None:
    """权益曲线数据源：(mode, initial_capital, [(ts, symbol, action, volume, price)...])。

    mode 交调用方判（非 paper → 400）；fills 按 ts 升序（现金流回算的顺序依赖）。
    None=任务不存在（调用方 404）。
    """
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        t = conn.execute("SELECT mode, initial_capital FROM live_task WHERE id=%s", (tid,)).fetchone()
        if t is None:
            return None
        fills = conn.execute(
            "SELECT ts, symbol, action, volume, price FROM paper_trade_log "
            "WHERE live_task_id=%s ORDER BY ts LIMIT %s", (tid, limit)).fetchall()
    return (t[0], t[1], fills)
