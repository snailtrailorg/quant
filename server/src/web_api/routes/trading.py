import json
import logging
import subprocess
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from src.data_platform.db import get_conn
from src.quant_common.redis_client import business_redis

# 批 86-B：纸上交易数据面下沉层 1（no_sql 闸门——routes 的 SQL 计数只许调低不许调高）
from src.data_platform.paper_trade import (
    get_task_equity_source, get_task_snapshot_params, get_virtual_account_id,
    list_paper_task_rows, update_task_fields)


from ..auth import assert_task_perm, audit_log, require_perm, require_perm_any
from ..errors import ApiError

logger = logging.getLogger("web_api")

router = APIRouter(tags=["trading"])

from src.data_platform.perm_registry import MARKET_OP_KEYS  # noqa: E402  # 延迟/位置语义 import（load_dotenv 后等）

LIVE_TRADING_MARKETS = MARKET_OP_KEYS   # 批55-0 L0:实盘开关键单源化(原独立硬编码副本——双源漂移=开关失控实弹风险)


@router.get("/api/live-task")
def list_live_tasks(status: str | None = None,
                    payload: dict = Depends(require_perm("live_control"))):
    """列**实盘**任务（批 86-B：读门由 `read` 收紧为 `live_control`）。

    **为什么读门要收紧（治理原则的落地）**：批 86-B 裁定「analyst 连实盘任务列表都不该看到」
    ——造策略的人≠验策略的人，不透明是刻意的（防「为通过而调参」，Goodhart 定律）。
    只在 nav 层隐藏是不够的：`live-task` 页面本身原先无 api 维门槛（只要 `read`），
    analyst 有 `read` ⇒ 直接敲 URL `/live-task` 仍能看到全部实盘任务细节。
    ⇒ **读与写同门 `live_control`**（有实盘权的人才有必要看实盘任务），语义干净且不新增键。

    ⚠️ 副作用（接受）：`viewer`/`analyst` 连只读都看不到实盘任务——**这正是要的严格性**。

    批 86-B：**只列实盘任务**（`mode='live'`）。paper 任务由 `GET /api/paper-trade` 提供，
    两者列表不混——否则 analyst 打开纸上交易页会看到实盘任务（等于绕过了上面的收紧）。
    """
    with get_conn() as conn:
        _base = ("SELECT lt.id, lt.name, lt.strategy_id, lt.symbol, lt.params, lt.status, "
                 "lt.account_id, lt.initial_capital, lt.created_at, ta.name "
                 "FROM live_task lt LEFT JOIN trading_account ta ON ta.id=lt.account_id ")
        _filter = "lt.mode='live'"
        if status:
            cur = conn.execute(
                _base + f"WHERE {_filter} AND lt.status=%s ORDER BY lt.id DESC", (status,))
        else:
            cur = conn.execute(_base + f"WHERE {_filter} ORDER BY lt.id DESC")
        rows = cur.fetchall()
    # P1-5（web-design 05 §5.8/06 B#5）：合并 worker 心跳（md_mode/lag/bars/frozen/gen）——
    # 任务"活着吗、行情新鲜吗、冻没冻"三问列表页直答
    hb = {}
    try:
        r_ = business_redis(decode_responses=True, socket_timeout=1)
        for rid, *_ in rows:
            h = r_.hgetall(f"quant:hb:task:{rid}")
            if h:
                hb[rid] = h
    except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
        pass
    out = []
    for r in rows:
        h = hb.get(r[0], {})
        out.append({"id": r[0], "name": r[1], "strategy_id": r[2], "symbol": r[3],
                    "params": json.loads(r[4]) if isinstance(r[4], str) else (r[4] or {}),
                    "status": r[5], "account_id": r[6], "initial_capital": float(r[7]) if r[7] else None,
                    "created_at": str(r[8]) if r[8] else None,
                    "account_name": r[9],
                    "md_mode": (h.get("md") if h else None) or (json.loads(r[4]) if isinstance(r[4], str) else (r[4] or {})).get("md_mode") or "hub",
                    "lag": float(h["lag"]) if h.get("lag") not in (None, "", "-1") else (float(h["lag"]) if h.get("lag") == "-1" else None),
                    "bars": int(h["bars"]) if h.get("bars") else 0,
                    "frozen": h.get("frozen") == "1",
                    "hb_age_s": (time.time() - float(h["ts"])) if h.get("ts") else None})
    return out


@router.post("/api/live-task")
def create_live_task(body: dict = Body(...),
                     payload: dict = Depends(require_perm_any("paper_trade", "live_control"))):
    """创建任务：选策略+标的+任务参数值。创建时构建 strategy_snapshot。

    批 86-B：body 新增 `mode`（`live`｜`paper`，缺省 `live`=完全现行为）。
    - `mode='paper'`（纸上交易）：**account_id 忽略/覆盖为虚拟账户**——风控预算隔离的落点
      （见 `0127` 注释）；门= `paper_trade`。
    - `mode='live'`（实盘）：门= `live_control`（原样）。
    两道门：粗筛在依赖（任一键），**分档判定在函数体**（按 mode 查所需键）。
    """
    from src.strategy_framework.strategy import build_default_params, validate_parameter_defs, validate_params_against_defs
    name = body.get("name", "")
    strategy_id = body.get("strategy_id", "")
    symbol = body.get("symbol", "")
    params = body.get("params", {})
    account_id = body.get("account_id")
    initial_capital = body.get("initial_capital", 1000000)
    # 批 86-B：mode（0125 的 CHECK 锁值域；此处再校验一次以给出可读 400 而非 DB 异常）
    mode = str(body.get("mode") or "live").lower()
    if mode not in ("live", "paper"):
        raise ApiError(400, "MODE_INVALID", f"mode 须为 live 或 paper，收到 {mode!r}")
    is_paper = mode == "paper"

    if not name or not strategy_id or not symbol:
        raise ApiError(400, "MISSING_FIELDS", "name/strategy_id/symbol 必填")

    # 批 86-B：分档权限判定（粗筛已在依赖层）。
    # paper 任务须 paper_trade；live 任务须 live_control（analyst 有前者无后者 ⇒ 建不了实盘任务）。
    _need = "paper_trade" if is_paper else "live_control"
    _role = payload.get("db_role") or payload.get("role", "viewer")
    from ..auth import load_effective_permissions as _lep
    _have, _ = _lep(payload.get("username", ""), _role)
    if _need not in _have:
        raise ApiError(403, "PERM_DENIED",
                       f"创建{'纸上' if is_paper else '实盘'}任务需要 {_need} 权限，角色 {_role} 无")

    if is_paper:
        # paper 任务**强制绑定虚拟账户**（忽略前端传入）——这是预算隔离的实现点，
        # 不可由客户端覆盖（否则「传一个真实 account_id 的 paper 任务」会污染实盘预算）。
        # 数据面在层 1（paper_trade.get_virtual_account_id）；None=0127 未执行，
        # 必须 500 显式报错而非静默回退到真实账户（隔离的最后一道保险）。
        account_id = get_virtual_account_id()
        if account_id is None:
            raise ApiError(500, "VIRTUAL_ACCOUNT_MISSING",
                           "虚拟账户不存在（迁移 0127 未执行？）——纸上任务无法创建")
    elif account_id is None:
        raise ApiError(400, "ACCOUNT_REQUIRED", "account_id 必填（交易账号）")
    if not isinstance(account_id, int) or isinstance(account_id, bool):
        raise ApiError(400, "ACCOUNT_ID_INVALID", "account_id 须为整数（交易账号 id）")

    # 读策略配置
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, name, type, symbol, adapter, enabled, factors, aggregator, risk, params, backtest_verified "
            "FROM strategy_config WHERE id=%s", (strategy_id,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "STRATEGY_NOT_FOUND", f"策略 {strategy_id} 不存在")
    if not row[10]:
        raise ApiError(403, "STRATEGY_NOT_VERIFIED", "策略未通过回测验证，禁止实盘")

    sc_params = json.loads(row[9]) if isinstance(row[9], str) else (row[9] or {})
    defs = sc_params.get("parameter_defs", [])

    # 校验参数定义
    err = validate_parameter_defs(defs)
    if err:
        raise ApiError(400, "PARAM_DEFS_INVALID", f"策略参数定义错误: {err}")

    # 合并默认值 + 用户传入参数
    merged_params = {**build_default_params(defs), **params}
    err = validate_params_against_defs(merged_params, defs)
    if err:
        raise ApiError(400, "PARAM_INVALID", f"参数值错误: {err}")

    # 构建策略快照（创建时固化，后续改策略不影响）
    strategy_snapshot = {
        "id": row[0], "name": row[1], "type": row[2],
        "adapter": row[4], "factors": row[6] if isinstance(row[6], list) else json.loads(row[6] or "[]"),
        "aggregator": row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}"),
        "risk": row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}"),
        "params": sc_params,  # 含 mode/python_code/parameter_defs
    }

    with get_conn() as conn:
        # D2：account 校验——存在且交易域（建任务绑定交易账号，delete_interface 受 FK RESTRICT 守卫）
        cur = conn.execute(
            "SELECT id FROM trading_account WHERE id=%s", (account_id,))
        if cur.fetchone() is None:
            raise ApiError(404, "ACCOUNT_NOT_FOUND", f"account {account_id} 不存在或非交易域")
        # D5 三级时点①：account 级品种权限（account_allows）——建任务时拒绝无权限品种
        from src.data_platform.perms import account_allows
        if not account_allows(account_id, symbol):
            raise ApiError(403, "ACCOUNT_NOT_ALLOWED",
                           f"account {account_id} 不允许交易品种 {symbol}（三维 category/exchange/board 权限）")
        cur = conn.execute(
            "INSERT INTO live_task (name, strategy_id, symbol, params, strategy_snapshot, status, "
            "account_id, initial_capital, owner_username, mode) "
            "VALUES (%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s) RETURNING id",
            (name, strategy_id, symbol, json.dumps(merged_params), json.dumps(strategy_snapshot),
             account_id, initial_capital, payload["username"], mode))
        task_id = cur.fetchone()[0]
        conn.commit()
    audit_log(payload["username"], "create_live_task",
              f"task {task_id} mode={mode} strategy={strategy_id} symbol={symbol}")
    return {"id": task_id, "status": "pending", "mode": mode}


@router.post("/api/live-task/{tid}/start")
def start_live_task(tid: int,
                    payload: dict = Depends(require_perm_any("paper_trade", "live_control"))):
    """启动任务（批 86-B：按任务 mode 分档判权——paper 须 paper_trade，live 须 live_control）。"""
    assert_task_perm(payload, tid, action="启动")
    with get_conn() as conn:
        cur = conn.execute("SELECT status, strategy_id FROM live_task WHERE id=%s", (tid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "LIVE_TASK_NOT_FOUND", "实盘任务不存在")
        conn.execute("UPDATE live_task SET status='running', updated_at=now() WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "start_live_task", f"task {tid}")
    try:
        subprocess.run(["systemctl", "start", f"quant-live-task@{tid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("start_live_task: systemctl start quant-live-task@%s 失败", tid, exc_info=True)
    return {"id": tid, "status": "running"}


@router.post("/api/live-task/{tid}/stop")
def stop_live_task(tid: int,
                   payload: dict = Depends(require_perm_any("paper_trade", "live_control"))):
    """停止任务（批 86-B：按 mode 分档判权，同 start）。"""
    assert_task_perm(payload, tid, action="停止")
    with get_conn() as conn:
        conn.execute("UPDATE live_task SET status='stopped', updated_at=now() WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "stop_live_task", f"task {tid}")
    try:
        subprocess.run(["systemctl", "stop", f"quant-live-task@{tid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("stop_live_task: systemctl stop quant-live-task@%s 失败", tid, exc_info=True)
    return {"id": tid, "status": "stopped"}


@router.get("/api/live-task/unfreeze-request")
def get_unfreeze_request(token: str = Query(...),
                         payload: dict = Depends(require_perm("live_control"))):
    """批 76 F2：IM 发起解冻的一次性确认 token 预览（Web 确认页展示用；**只读不消费**）。

    确认链=IM 发起（确认卡闸：身份+unfreeze 档+去重）→ bot 回执带 token 深链 → 管理员在 Web
    登录态打开本页 → 确认后 POST /api/live-task/{tid}/unfreeze 带同一 token（此刻才消费）。
    """
    from src.data_platform import freeze_event as _fe
    info = _fe.peek_confirm_token(token)
    if not info:
        raise ApiError(400, "UNFREEZE_TOKEN_INVALID", "确认链接已失效或已被使用，请在 IM 重新发起")
    return {"tid": info.get("tid"), "symbol": info.get("symbol", ""),
            "freeze_type": info.get("freeze_type", ""), "initiator": info.get("operator", ""),
            "created_at": info.get("created_at")}


@router.post("/api/live-task/{tid}/unfreeze")
def unfreeze_live_task(tid: int, body: dict = Body(default=None),
                       payload: dict = Depends(require_perm_any("paper_trade", "live_control"))):
    """批 76 F2 解冻面·Web 入口：人工解冻（**不重启任务**）——写 Valkey 请求键，worker 5s 内消费。

    批 86-B：按任务 mode 分档判权（paper 须 paper_trade）。**paper 任务同样需要解冻**——
    它也消费实时行情流 ⇒ 也会有 gap/污染冻窗（冻结语义对 paper 照常适用，见设计文档 §六）。

    两形态同端点：
    - Web 直接操作（无 token）→ `manual_web`；
    - IM 发起→Web 确认链（带 `confirm_token`）→ `manual_im`：token 一次性消费（GETDEL）且
      必须与路径 tid 一致（防「A 任务的确认链接解冻 B 任务」）。
    语义=操作者**显式接受当前数据状态**（带洞窗/污染窗），故必须留操作者与通道（审计）。
    """
    from src.data_platform import freeze_event as _fe
    assert_task_perm(payload, tid, action="解冻")
    body = body or {}
    with get_conn() as conn:
        row = conn.execute("SELECT status, symbol FROM live_task WHERE id=%s", (tid,)).fetchone()
    if not row:
        raise ApiError(404, "LIVE_TASK_NOT_FOUND", "实盘任务不存在")
    token = body.get("confirm_token")
    channel = "manual_web"
    operator = payload["username"]
    if token:
        info = _fe.consume_confirm_token(token)
        if not info:
            raise ApiError(400, "UNFREEZE_TOKEN_INVALID", "确认链接已失效或已被使用，请在 IM 重新发起")
        if int(info.get("tid") or -1) != int(tid):
            raise ApiError(400, "UNFREEZE_TOKEN_MISMATCH", "确认链接与目标任务不一致，token 已作废")
        channel = "manual_im"
        operator = f"{payload['username']}（IM 发起:{info.get('operator') or '?'}）"
    try:
        _fe.request_unfreeze(tid, operator=operator, channel=channel)
    except Exception as e:
        logger.error("unfreeze_live_task: 解冻请求写入失败 tid=%s", tid, exc_info=True)
        raise ApiError(503, "UNFREEZE_CHANNEL_UNAVAILABLE",
                       "解冻通道不可用（Valkey），请稍后重试") from e
    audit_log(payload["username"], "unfreeze_live_task", f"task {tid} channel={channel}")
    return {"ok": True, "tid": tid, "channel": channel, "symbol": row[1], "status": row[0]}


@router.get("/api/live-task/{tid}/freeze-events")
def list_freeze_events(tid: int, limit: int = 50,
                       payload: dict = Depends(require_perm_any("paper_trade", "live_control"))):
    """批 76 F1：冻结事件事实（最近 limit 条；进行中= unfrozen_at 为 null）。

    批 86-B：读门由 `read` 收紧为「按任务 mode 分档」——冻结事件含任务运行细节
    （冻因/水位/缺口目标时刻），属治理原则要挡住 analyst 的那类信息（可推知策略实证表现）。
    """
    from src.data_platform import freeze_event as _fe
    assert_task_perm(payload, tid, action="查看冻结事件")
    return {"events": _fe.list_events(task_id=tid, limit=min(max(limit, 1), 200))}


@router.get("/api/freeze-events")
def list_all_freeze_events(open_only: bool = False, limit: int = 50,
                           payload: dict = Depends(require_perm("read"))):
    """批 114：全局冻结事件（顶栏冻结入口角标/下拉的数据源）。

    与 per-task `GET /api/live-task/{tid}/freeze-events` 的区别：**跨任务、无 mode 分档**——
    入口图标的目标是「把当前冻着的任务整体列出来」，不是某任务的运行细节。故权限面回到
    `read`（四角色可见）——角标「有几个任务冻着」是全局健康信号，与铃铛的 admin-only 告警面
    不同源也不同门。`open_only=true` 只取未闭合（`unfrozen_at IS NULL`），角标计数用；
    默认 false 行为=全量最近 limit 条。

    批 86-B 治理（步 4 复审 P0-1 返工）：**投影最小字段集**——只回 `id`/`task_id`/`symbol`/
    `freeze_type`/`frozen_at`。水位 `watermark`/缺口目标时刻 `gap_target_ts`/`account_id`/
    `operator`/`unfreeze_method` 等运行细节**不投影**（它们属「可推知策略实证表现」的敏感面，
    per-task 端点正是为挡 analyst 才收紧到 paper_trade/live_control）；明细回 per-task 分档端点。
    """
    from src.data_platform import freeze_event as _fe
    events = _fe.list_events(task_id=None, limit=min(max(limit, 1), 200),
                             open_only=open_only)
    return {"events": [{"id": e["id"], "task_id": e["task_id"], "symbol": e["symbol"],
                        "freeze_type": e["freeze_type"], "frozen_at": e["frozen_at"]}
                       for e in events]}


@router.delete("/api/live-task/{tid}")
def delete_live_task(tid: int,
                     payload: dict = Depends(require_perm_any("paper_trade", "live_control"))):
    """删除任务（仅 stopped/error 可删）。批 86-B：按 mode 分档判权。"""
    assert_task_perm(payload, tid, action="删除")
    with get_conn() as conn:
        cur = conn.execute("SELECT status FROM live_task WHERE id=%s", (tid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "LIVE_TASK_NOT_FOUND", "实盘任务不存在")
        if row[0] == "running":
            raise ApiError(400, "LIVE_TASK_RUNNING", "运行中的任务不可删除，请先停止")
        conn.execute("DELETE FROM live_task WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "delete_live_task", f"task {tid}")
    return {"ok": True}


@router.put("/api/live-task/{tid}")
def update_live_task(tid: int, body: dict = Body(...),
                     payload: dict = Depends(require_perm_any("paper_trade", "live_control"))):
    """批 86-B 新增：编辑任务（改 name/params/initial_capital）。

    ⚠️ **不可改 `strategy_id` / `symbol` / `account_id` / `mode`**：它们决定已启动进程的身份
    （systemd 单元名绑 tid、策略快照在建任务时固化、账户决定风控预算归属）——
    改它们须「删掉重建」，不是编辑。传了就 400（显式拒绝优于静默忽略）。

    权限：按任务 mode 分档（`assert_task_perm`）。**特别是防 analyst 通过编辑绕到实盘任务上**——
    若不判 mode，analyst 拿 `paper_trade` 过粗筛后可以 PATCH 一个 live 任务的参数。
    """
    assert_task_perm(payload, tid, action="编辑")
    _immutable = [k for k in ("strategy_id", "symbol", "account_id", "mode") if k in body]
    if _immutable:
        raise ApiError(400, "FIELD_IMMUTABLE",
                       f"字段不可编辑（决定任务身份，请删除后重建）: {','.join(_immutable)}")
    _fields: dict = {}
    if "name" in body:
        if not str(body["name"]).strip():
            raise ApiError(400, "NAME_INVALID", "name 不可为空")
        _fields["name"] = body["name"]
    if "initial_capital" in body:
        try:
            ic = float(body["initial_capital"])
        except (TypeError, ValueError):
            raise ApiError(400, "INITIAL_CAPITAL_INVALID", "initial_capital 须为数值") from None
        _fields["initial_capital"] = ic
    if "params" in body:
        # 参数校验与建任务同源（策略快照里固化的 parameter_defs 是真源）——防编辑绕过校验写入非法值。
        # 快照读取在层 1（paper_trade.get_task_snapshot_params）。
        from src.strategy_framework.strategy import (
            build_default_params, validate_parameter_defs, validate_params_against_defs)
        _r = get_task_snapshot_params(tid)
        if _r is None:
            raise ApiError(404, "LIVE_TASK_NOT_FOUND", "任务不存在")
        _snap = _r[0] if isinstance(_r[0], dict) else json.loads(_r[0] or "{}")
        _defs = (_snap.get("params") or {}).get("parameter_defs", [])
        _err = validate_parameter_defs(_defs)
        if _err:
            raise ApiError(400, "PARAM_DEFS_INVALID", f"策略参数定义错误: {_err}")
        _merged = {**build_default_params(_defs), **(body["params"] or {})}
        _err = validate_params_against_defs(_merged, _defs)
        if _err:
            raise ApiError(400, "PARAM_INVALID", f"参数值错误: {_err}")
        _fields["params"] = json.dumps(_merged)
    if not _fields:
        raise ApiError(400, "NOTHING_TO_UPDATE", "无可更新字段（name/params/initial_capital）")
    updated = update_task_fields(tid, _fields)
    if not updated:
        raise ApiError(404, "LIVE_TASK_NOT_FOUND", "任务不存在")
    audit_log(payload["username"], "update_live_task",
              f"task {tid} fields={','.join(k for k in body if k in ('name','params','initial_capital'))}")
    return {"ok": True, "id": tid}


@router.get("/api/paper-trade")
def list_paper_tasks(status: str | None = None,
                     payload: dict = Depends(require_perm("paper_trade"))):
    """批 86-B：列**纸上交易**任务（`mode='paper'`）。

    与 `GET /api/live-task` 是两个端点、两个门——这是刻意的：
    - `live-task` 门 = `live_control`（实盘面，analyst/viewer 全不可见）
    - `paper-trade` 门 = `paper_trade`（analyst 可见可用，这是他的「策略前测」入口）

    返回体与实盘任务列表同形（前端可复用列定义与心跳展示），仅数据源不同。
    """
    rows = list_paper_task_rows(status)   # 数据面在层 1（no_sql 闸门）
    hb = {}
    try:
        r_ = business_redis(decode_responses=True, socket_timeout=1)
        for rid, *_ in rows:
            h = r_.hgetall(f"quant:hb:task:{rid}")
            if h:
                hb[rid] = h
    except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
        pass
    out = []
    for r in rows:
        h = hb.get(r[0], {})
        out.append({"id": r[0], "name": r[1], "strategy_id": r[2], "symbol": r[3],
                    "params": json.loads(r[4]) if isinstance(r[4], str) else (r[4] or {}),
                    "status": r[5], "account_id": r[6],
                    "initial_capital": float(r[7]) if r[7] else None,
                    "created_at": str(r[8]) if r[8] else None,
                    "account_name": r[9], "mode": "paper",
                    "bars": int(h["bars"]) if h.get("bars") else 0,
                    "frozen": h.get("frozen") == "1",
                    "hb_age_s": (time.time() - float(h["ts"])) if h.get("ts") else None})
    return out


@router.get("/api/paper-trade/{tid}/equity")
def paper_task_equity(tid: int, limit: int = 2000,
                      payload: dict = Depends(require_perm("paper_trade"))):
    """批 86-B：纸上任务权益曲线（「查看结果」的数据源）。

    从 `paper_trade_log` 回算**累计已实现现金流**（SELL 收，BUY 付，按价×量），
    以任务 `initial_capital` 为起点产生逐点权益。

    ⚠️ **这是简化曲线，不是逐笔盯市**：它不读虚拟账户的 `position_snapshot`
    做浮动盈亏（那需要行情快照，而快照只对运行中任务新鲜）。本端点给的是
    **已实现**曲线 + 当前虚拟持仓快照，二者并列呈现——前端须**明确标注**
    「已实现」与「浮动」分开，不得合成一条「总权益」（会误导成精确盈亏）。
    """
    _src = get_task_equity_source(tid, min(max(limit, 1), 10000))   # 数据面在层 1
    if _src is None:
        raise ApiError(404, "TASK_NOT_FOUND", f"任务 {tid} 不存在")
    mode, init_raw, fills = _src
    if (mode or "live") != "paper":
        raise ApiError(400, "NOT_PAPER_TASK", f"任务 {tid} 不是纸上交易任务")
    init = float(init_raw or 0)
    cash = init
    pts, realized = [], 0.0
    for ts, sym, act, vol, px in fills:
        amt = float(vol or 0) * float(px or 0)
        cash += amt if str(act).upper() == "SELL" else -amt
        realized = cash - init
        pts.append({"ts": str(ts), "equity": cash, "realized": realized,
                    "symbol": sym, "action": act})
    return {"task_id": tid, "initial_capital": init, "points": pts,
            "realized_pnl": realized, "trade_count": len(fills),
            "note": "已实现现金流曲线（不含浮动盈亏）；成交为纸上模拟，未进入市场"}


@router.get("/api/live-trading")
def list_live_trading(payload: dict = Depends(require_perm("read"))):
    """列实盘分项开关 + .env 总闸状态。三级 AND：总闸 AND 分项 AND 策略 enabled。"""
    from src.data_platform.settings import is_live_trading_enabled
    with get_conn() as conn:
        cur = conn.execute("SELECT market, enabled, updated_at FROM live_trading_config ORDER BY market")
        rows = cur.fetchall()
    return {
        "master_enabled": is_live_trading_enabled(),
        "items": [{"market": r[0], "enabled": r[1], "updated_at": r[2]} for r in rows],
    }


@router.post("/api/live-trading/{market}")
def update_live_trading(market: str, enabled: bool = Query(...),
                        payload: dict = Depends(require_perm("live_trading_control"))):
    """开/关某品种实盘分项（trader/admin）。需 .env 总闸也开才真生效。"""
    if market not in LIVE_TRADING_MARKETS:
        raise HTTPException(400, f"未知市场: {market}，可选: {LIVE_TRADING_MARKETS}")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE live_trading_config SET enabled=%s, updated_at=now() WHERE market=%s RETURNING enabled",
            (enabled, market))
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, f"市场不存在: {market}")
    audit_log(payload["username"], "live_trading_toggle", detail=f"{market}={enabled}")
    return {"market": market, "enabled": row[0]}


def _account_state(conn):
    """D2：读每 account 最新快照 + 首条基线 + account 名（全局视图端点复用，决策1「按 account 分组」）。

    返回 list[dict]，每项含 account_id/account_name/total_value/daily_pnl/initial。
    baseline（initial）= 该 account 首条快照 total_value（#10 口径）；无历史退回快照列值/默认 100 万。
    """
    cur = conn.execute(
        "SELECT DISTINCT ON (account_id) account_id, total_value, daily_pnl, initial_capital "
        "FROM account_snapshot ORDER BY account_id, ts DESC")
    latest = cur.fetchall()
    cur = conn.execute(
        "SELECT DISTINCT ON (account_id) account_id, total_value "
        "FROM account_snapshot ORDER BY account_id, ts ASC")
    first_map = {r[0]: (float(r[1]) if r[1] is not None else None) for r in cur.fetchall()}
    cur = conn.execute("SELECT id, name FROM trading_account")
    name_map = {r[0]: r[1] for r in cur.fetchall()}
    out = []
    for r in latest:
        vid = r[0]
        first = first_map.get(vid)
        initial = first if first is not None else (float(r[3]) if r[3] is not None else 1000000.0)
        out.append({
            "account_id": vid, "account_name": name_map.get(vid),
            "total_value": float(r[1]) if r[1] is not None else 0.0,
            "daily_pnl": float(r[2]) if r[2] is not None else 0.0,
            "initial": initial,
        })
    return out


@router.get("/api/position")
def get_position(payload: dict = Depends(require_perm("read"))):
    """当前持仓（ST2：券商 position_snapshot 快照=真相源；trade_log 推导已挪 /api/reconcile 归因）。

    D2（决策1）：按 account 分组返回——accounts=per-account 明细，顶层 total_value/total_pnl/
    total_pnl_pct=跨 account 聚合摘要（前端可粗显）。stale 语义（N-S5）：position_refresh.ts
    距今 >600s 或从未写过 → stale=True。
    """
    import datetime as _pdt
    with get_conn() as conn:
        accounts_state = _account_state(conn)
        cur = conn.execute(
            "SELECT account_id, symbol, direction, volume, frozen, cost_price, pnl "
            "FROM position_snapshot WHERE volume != 0 ORDER BY account_id, symbol")
        pos_by_account: dict = {}
        for r in cur.fetchall():
            pos_by_account.setdefault(r[0], []).append({
                "symbol": r[1], "direction": r[2], "volume": int(r[3]),
                "frozen": int(r[4] or 0),
                "cost_price": float(r[5]) if r[5] is not None else None,
                "pnl": float(r[6]) if r[6] is not None else None})
        cur = conn.execute("SELECT account_id, ts, rows FROM position_refresh")
        refresh_by_account = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    now = _pdt.datetime.now(_pdt.timezone.utc)
    accounts = []
    total_value = total_pnl = total_initial = 0.0
    for vs in accounts_state:
        vid = vs["account_id"]
        initial = vs["initial"]
        pnl = vs["total_value"] - initial
        total_value += vs["total_value"]
        total_initial += initial
        total_pnl += pnl
        ref = refresh_by_account.get(vid)
        stale = True
        snapshot_ts = None
        if ref and ref[0] is not None:
            ts_aware = ref[0] if ref[0].tzinfo else ref[0].replace(tzinfo=_pdt.timezone.utc)
            snapshot_ts = ts_aware.isoformat()
            stale = (now - ts_aware).total_seconds() > 600
        accounts.append({
            "account_id": vid, "account_name": vs["account_name"],
            "total_value": vs["total_value"], "total_pnl": pnl,
            "total_pnl_pct": round(pnl/initial*100, 2) if initial else 0,
            "positions": pos_by_account.get(vid, []),
            "snapshot_ts": snapshot_ts,
            "snapshot_rows": ref[1] if ref else 0,
            "stale": stale})
    return {"accounts": accounts,
            "total_value": total_value, "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl/total_initial*100, 2) if total_initial else 0,
            "stale": any(v["stale"] for v in accounts)}


@router.get("/api/pnl")
def get_pnl(payload: dict = Depends(require_perm("read"))):
    """盈亏曲线（account_snapshot 时间序列，#6）。D2（决策1）：按 account 分组返回。"""
    with get_conn() as conn:
        accounts_state = _account_state(conn)
        cur = conn.execute(
            "SELECT account_id, ts, total_value, daily_pnl FROM ("
            "  SELECT account_id, ts, total_value, daily_pnl, "
            "         ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY ts DESC) AS rn "
            "  FROM account_snapshot"
            ") sub WHERE rn <= 90 ORDER BY account_id, ts DESC")
        rows = cur.fetchall()
    curve_by_account: dict = {}
    for r in rows:
        curve_by_account.setdefault(r[0], []).append({
            "ts": r[1].isoformat() if r[1] else "",
            "value": float(r[2]) if r[2] is not None else 0,
            "daily_pnl": float(r[3]) if r[3] is not None else 0})
    accounts = []
    total_pnl = total_initial = today_pnl = 0.0
    for vs in accounts_state:
        vid = vs["account_id"]
        curve = list(reversed(curve_by_account.get(vid, [])[:90]))  # 近 90 条，升序（旧→新）
        initial = vs["initial"]
        pnl = (curve[-1]["value"] - initial) if curve else 0
        accounts.append({
            "account_id": vid, "account_name": vs["account_name"],
            "curve": curve,
            "today_pnl": curve[-1]["daily_pnl"] if curve else 0,
            "total_pnl": pnl,
            "total_pnl_pct": round(pnl/initial*100, 2) if initial else 0})
        total_initial += initial
        total_pnl += pnl
        today_pnl += (curve[-1]["daily_pnl"] if curve else 0)
    return {"accounts": accounts,
            "today_pnl": today_pnl, "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl/total_initial*100, 2) if total_initial else 0}


@router.get("/api/orders")
def get_orders(payload: dict = Depends(require_perm("read"))):
    """订单记录（order_log 最近 100，#6）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT ts, strategy_id, symbol, action, volume, price, status, client_order_id, error "
                           "FROM order_log ORDER BY ts DESC LIMIT 100")   # wd-20 §1.4.3：补委托号/失败原因（0039 列）
        rows = cur.fetchall()
    return {"orders": [{"ts": r[0].isoformat() if r[0] else "", "strategy_id": r[1], "symbol": r[2], "action": r[3], "volume": r[4], "price": float(r[5]) if r[5] else 0, "status": r[6],
                        "client_order_id": r[7], "error": r[8]} for r in rows],
            "total": len(rows)}


@router.get("/api/account")
def list_accounts(payload: dict = Depends(require_perm("read"))):   # 批55b:LiveTask 建任务下拉依赖(viewer 可见掩码提示)
    """列券商/交易所账户（密钥不返回明文）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM accounts LIMIT 1")
        except Exception:
            logger.warning("list_accounts: accounts 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT id, name, exchange, api_key_hint, enabled, created_at FROM accounts ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "exchange": r[2], "api_key_hint": r[3],
             "enabled": r[4], "created_at": str(r[5])} for r in rows]


@router.post("/api/account")
def create_account(req: dict = Body(...), payload: dict = Depends(require_perm("system_config"))):   # 批55b:与集成中心页签门一致
    """P4-5 创建账户。"""
    with get_conn() as conn:
        k = (req.get("name", ""), req.get("exchange", ""), req.get("api_key_hint", ""), req.get("enabled", True))
        cur = conn.execute("INSERT INTO accounts (name, exchange, api_key_hint, enabled) VALUES (%s,%s,%s,%s) RETURNING id", k)
        conn.commit()
        return {"id": cur.fetchone()[0]}


@router.get("/api/account/{aid}")
def get_account(aid: int, payload: dict = Depends(require_perm("read"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, exchange, api_key_hint, enabled, created_at FROM accounts WHERE id=%s", (aid,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "账户不存在")
    return {"id": row[0], "name": row[1], "exchange": row[2], "api_key_hint": row[3], "enabled": row[4]}


@router.post("/api/account/{aid}")
def update_account(aid: int, req: dict = Body(...), payload: dict = Depends(require_perm("system_config"))):
    """P4-5 更新账户。"""
    with get_conn() as conn:
        for k in ("name", "exchange", "api_key_hint", "enabled"):
            if k in req:
                conn.execute(f"UPDATE accounts SET {k}=%s WHERE id=%s", (req[k], aid))
        conn.commit()
    return {"ok": True}


@router.delete("/api/account/{aid}")
def delete_account(aid: int, payload: dict = Depends(require_perm("system_config"))):
    """P4-5 删除账户。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id=%s", (aid,))
        conn.commit()
    return {"ok": True}


@router.get("/api/dashboard")
def get_dashboard(payload: dict = Depends(require_perm("read"))):
    """Dashboard 量化指标（account_snapshot + 回测绩效，#10）。D2（决策1）：按 account 分组返回。"""
    with get_conn() as conn:
        accounts_state = _account_state(conn)
        cur = conn.execute("SELECT COUNT(*) FROM backtest_runs WHERE status='done'")
        bt = cur.fetchone()
    accounts = []
    total_value = total_pnl = total_initial = daily_pnl = 0.0
    for vs in accounts_state:
        initial = vs["initial"]
        pnl = vs["total_value"] - initial
        accounts.append({
            "account_id": vs["account_id"], "account_name": vs["account_name"],
            "total_value": vs["total_value"], "total_pnl": pnl,
            "total_pnl_pct": round(pnl/initial*100, 2) if initial else 0,
            "daily_pnl": vs["daily_pnl"]})
        total_value += vs["total_value"]
        total_initial += initial
        total_pnl += pnl
        daily_pnl += vs["daily_pnl"]
    return {"accounts": accounts,
            "total_value": total_value, "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl/total_initial*100, 2) if total_initial else 0,
            "daily_pnl": daily_pnl, "backtest_count": bt[0] if bt else 0}
