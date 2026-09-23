"""平台化管理路由：外部接口（批55 统一表）/限流四层/任务 —— 从 main.py 提取的 mgmt 端点。

批55a（27 号架构文档）：data_source_config+broker_config 合并为 external_interface——
行=账号/列=能力/页签=过滤视图。批55b：旧端点垫片已随前端切换删除（批39 channels 同款）。
"""

import json

from fastapi import APIRouter, Depends, Request, Body, HTTPException
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import InterfaceReq, InterfaceReorderReq, RateLimitOverrideReq, VenuePermissionReq
from src.data_platform.db import get_conn
import logging

logger = logging.getLogger("web_api")

router = APIRouter(tags=["mgmt"])


# --- 外部接口（批55a：行=账号/列=能力/页签=过滤视图——27 号架构文档） ---

# 域谓词（分域段）：trading∈capabilities=交易域；其余=数据域（域内 position 独立 0..n）
_DOMAIN_TRADING = "'trading' = ANY(capabilities)"
_DOMAIN_DATA = "NOT ('trading' = ANY(capabilities))"


def _iface_row(r, with_code_caps: bool = True) -> dict:
    """DB 行 → API 形状（params=jsonb 读侧已是 dict；附 code_capabilities=能力真源）。"""
    d = {"id": r[0], "name": r[1], "provider": r[2], "market": r[3],
         "exchanges": list(r[4]) if r[4] else None,
         "has_credentials": bool(r[5]), "params": r[6],
         "capabilities": list(r[7]), "position": r[8], "enabled": r[9],
         "updated_at": str(r[10]) if r[10] else None,
         "account_key": r[11]}   # D2：语义键（资金账号，可空）
    if with_code_caps:
        from src.data_platform.capabilities import provider_capabilities
        d["code_capabilities"] = sorted(provider_capabilities(r[2]))
    return d


_IFACE_COLS = ("id, name, provider, market, exchanges, credentials_encrypted IS NOT NULL, "
               "params, capabilities, position, enabled, updated_at, account_key")


def _is_unique_violation(e: Exception) -> bool:
    """psycopg3 唯一约束冲突（SQLSTATE 23505）——account_key UNIQUE(provider,account_key) 判重。"""
    return getattr(e, "sqlstate", None) == "23505"


def _is_fk_violation(e: Exception) -> bool:
    """psycopg3 外键约束冲突（SQLSTATE 23503）——delete_interface 守卫兜底。"""
    return getattr(e, "sqlstate", None) == "23503"


def _default_exchanges(provider: str) -> list[str] | None:
    """perp 类 provider 的默认单所覆盖（盲审 B-P2：不写 exchanges 则 NULL=全所语义错——
    binance_perp 的 NULL 会含 OKX）。推导源=MARKET_OP_DECOMP 的 venue；其余 None=全所。"""
    from src.quant_common.markets import MARKET_OP_DECOMP
    d = MARKET_OP_DECOMP.get(provider)
    return [d[2]] if d and d[2] else None


def _validate_iface(provider: str, market: str, exchanges, capabilities) -> tuple[list, list | None]:
    """写侧校验（方案一 v2 六必修）：market∈MARKETS 且=PROVIDER_MARKET[provider]；
    exchanges⊆市场全所；capabilities 非空且⊆代码能力（越集 400）。返回规范化 (caps, exchanges)。"""
    from src.quant_common.markets import MARKETS, EXCHANGES, PROVIDER_MARKET
    from src.data_platform.capabilities import check_capability_subset
    if market not in MARKETS:
        raise ApiError(400, "IFACE_MARKET_UNKNOWN", f"未知市场 {market}（注册表：{sorted(MARKETS)}）")
    expect = PROVIDER_MARKET.get(provider)
    if expect is None:
        raise ApiError(400, "IFACE_PROVIDER_UNKNOWN",
                       f"provider {provider} 未注册（需先实现接入并登记 markets.PROVIDER_MARKET）")
    if market != expect:
        raise ApiError(400, "IFACE_MARKET_MISMATCH",
                       f"provider {provider} 归属市场 {expect}，与提交 {market} 不符")
    caps = set(capabilities or [])
    if not caps:
        raise ApiError(400, "IFACE_CAP_EMPTY", "capabilities 不能为空（至少启用一项能力）")
    ok, msg = check_capability_subset(provider, caps)
    if not ok:
        raise ApiError(400, "IFACE_CAP_EXCESS", msg)
    if exchanges is None:
        exchanges = _default_exchanges(provider)   # perp 缺省=单所（防 NULL 全所语义错）
    if exchanges:
        market_ex = {e for e, v in EXCHANGES.items() if v["market"] == market}
        bad = set(exchanges) - market_ex
        if bad:
            raise ApiError(400, "IFACE_EXCHANGE_UNKNOWN",
                           f"交易所 {sorted(bad)} 不属于市场 {market}（该市场全所：{sorted(market_ex)}）")
        # 盲审 B-P1-4：perp 类 provider 只能覆盖自身 venue（防 binance 通道勾 OKX 的语义错行）
        from src.quant_common.markets import MARKET_OP_DECOMP
        d = MARKET_OP_DECOMP.get(provider)
        if d and d[2]:
            bad = set(exchanges) - {d[2]}
            if bad:
                raise ApiError(400, "IFACE_EXCHANGE_UNKNOWN",
                               f"provider {provider} 仅覆盖 {[d[2]]}（不可勾 {sorted(bad)}）")
    return sorted(caps), (list(exchanges) if exchanges else None)


def _validate_credentials(provider: str, credentials: str) -> None:
    """写侧凭证校验（批63）：provider 有 FIELD_SCHEMA 且 credentials 非空时，
    须合法 JSON 对象且 secret 字段非空（对标 IM required_fields）；无 schema 的 stub provider 跳过。"""
    from src.data_platform.interfaces import get_interface_provider
    inst = get_interface_provider(provider)
    if not inst or not inst.FIELD_SCHEMA or not credentials:
        return
    try:
        cred = json.loads(credentials)
    except (TypeError, ValueError):
        raise ApiError(400, "IFACE_CRED_INVALID", "credentials 须为合法 JSON 对象")
    if not isinstance(cred, dict):
        raise ApiError(400, "IFACE_CRED_INVALID", "credentials 须为 JSON 对象")
    missing = [f for f in inst.required_fields if not cred.get(f)]
    if missing:
        raise ApiError(400, "IFACE_CRED_REQUIRED", f"缺少必填凭证字段：{missing}")


def _normalize_params(params) -> dict:
    """params 归一为 dict（str=旧端点 JSON 字符串惯例/新端点对象皆可；非法 400）。

    盲审 A-P1：合法 JSON 但非对象（数组/null/标量）同拒——错误码化而非 500。
    """
    if params is None or params == "":
        return {}
    if isinstance(params, dict):
        return params
    try:
        v = json.loads(params)
    except (TypeError, ValueError):
        raise ApiError(400, "IFACE_PARAMS_INVALID", "params 须为合法 JSON")
    if not isinstance(v, dict):
        raise ApiError(400, "IFACE_PARAMS_INVALID", "params 须为 JSON 对象")
    return v


@router.get("/api/interfaces")
def list_interfaces(cap: str | None = None, payload: dict = Depends(require_perm("read"))):
    """外部接口列表（页签=能力过滤视图：?cap=daily|minute|snapshot|quote|trading）。

    附漂移告警（55-0 共同底座：配置能力不在代码能力集=注册表防漂移闸同模式，批33b 先例）。
    """
    from src.data_platform.capabilities import check_capability_subset
    sql = f"SELECT {_IFACE_COLS} FROM external_interface"
    args: tuple = ()
    if cap:
        sql += " WHERE capabilities @> ARRAY[%s]::text[]"
        args = (cap,)
    sql += " ORDER BY position, id"
    with get_conn() as conn:
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
    items = []
    for r in rows:
        ok, msg = check_capability_subset(r[2], set(r[7]))
        if not ok:
            logger.warning("外部接口 %s(id=%s) 能力漂移：%s", r[2], r[0], msg)
        items.append(_iface_row(r))
    return items


@router.get("/api/interfaces/providers")   # 静态路由前移防 {iid} 遮蔽（批43 P0-3 同款）
def list_interface_providers(payload: dict = Depends(require_perm("read"))):
    """provider 目录（55b：新建弹窗下拉零硬编码——注册表派生）。

    provider→{market, capabilities=代码能力全集}；代码能力空（stub：joinquant/ricequant）
    不出目录（写侧 ⊆ 校验建不了行，列出来只会引导用户撞 400）。
    """
    from src.quant_common.markets import PROVIDER_MARKET, EXCHANGES
    from src.data_platform.capabilities import provider_capabilities
    from src.data_platform.interfaces import list_interface_schemas
    schemas = list_interface_schemas()
    return {"providers": [
        {"provider": p, "market": m, "capabilities": sorted(provider_capabilities(p)),
         "market_exchanges": sorted(e for e, v in EXCHANGES.items() if v["market"] == m),
         "default_exchanges": _default_exchanges(p),   # 批55b 盲审修：perp 预填单所（防"不选=全部"文案与后端钉默认不一致）
         "field_schema": schemas.get(p, {}).get("field_schema", []),      # 批63：凭证字段 schema（前端动态表单）
         "params_schema": schemas.get(p, {}).get("params_schema", [])}    # 批63：参数字段 schema
        for p, m in sorted(PROVIDER_MARKET.items()) if provider_capabilities(p)
    ]}


@router.post("/api/interfaces")
def create_interface(req: InterfaceReq, payload: dict = Depends(require_perm("system_config"))):
    from src.quant_common.crypto import encrypt
    caps, exchanges = _validate_iface(req.provider, req.market, req.exchanges, req.capabilities)
    params = _normalize_params(req.params)
    _validate_credentials(req.provider, req.credentials)   # 批63：secret 字段必填（对标 IM）
    enc = encrypt(req.credentials) if req.credentials else None
    domain = _DOMAIN_TRADING if "trading" in caps else _DOMAIN_DATA
    with get_conn() as conn:
        try:
            cur = conn.execute(
                f"INSERT INTO external_interface "
                f"(name, provider, market, exchanges, credentials_encrypted, params, capabilities, position, enabled, account_key) "
                f"VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,"
                f"(SELECT coalesce(max(position),-1)+1 FROM external_interface WHERE {domain}),%s,%s) RETURNING id",
                (req.name, req.provider, req.market, exchanges, enc,
                 json.dumps(params, ensure_ascii=False), caps, req.enabled, req.account_key))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if _is_unique_violation(e):
                raise ApiError(409, "IFACE_ACCOUNT_KEY_DUP",
                               f"account_key {req.account_key!r} 已被 provider {req.provider} 占用")
            raise
    audit_log(payload["username"], "interface_create", f"{req.provider} caps={caps}")
    return {"id": cur.fetchone()[0]}


@router.post("/api/interfaces/reorder")   # 路由前移防 {iid} int 遮蔽 422（批43 P0-3 教训）
def interfaces_reorder(req: InterfaceReorderReq, payload: dict = Depends(require_perm("system_config"))):
    """分域拖拽重排（批43 全集语义×方案一 v2 分域段正交）：body={domain, ids} 全量有序数组。

    域内全集校验（防并发丢行/幽灵 id）；域外行不动——XTP 等交易域行在数据页签可见但
    不可拖（其 position 属交易域——行的边界=账号）。幂等；并发=后写赢（低频管理操作）。
    """
    if req.domain == "trading":
        frag = _DOMAIN_TRADING
    elif req.domain == "data":
        frag = _DOMAIN_DATA
    else:
        raise ApiError(400, "IFACE_DOMAIN_INVALID", "domain 须为 data 或 trading")
    if not req.ids or not all(isinstance(i, int) for i in req.ids):
        raise ApiError(400, "BAD_PARAM", "ids 须为非空整数数组")
    with get_conn() as conn:
        cur = conn.execute(f"SELECT id FROM external_interface WHERE {frag}")
        existing = {r[0] for r in cur.fetchall()}
        if set(req.ids) != existing or len(req.ids) != len(existing):
            raise ApiError(400, "BAD_PARAM",
                           f"ids 必须等于 {req.domain} 域当前全部接口 id（全量序列——防并发丢行）")
        for pos, rid in enumerate(req.ids):
            conn.execute("UPDATE external_interface SET position=%s, updated_at=now() WHERE id=%s",
                         (pos, rid))
        conn.commit()
    try:
        from src.data_platform.routing import bump_config_version
        bump_config_version()   # 批 57：position 改动 bump（28 §6.3——epoch 生效验收②闭环）
    except Exception:
        pass
    audit_log(payload["username"], "interface_reorder", detail=f"{req.domain} order={req.ids}")
    return {"ok": True}


@router.post("/api/interfaces/{iid}")
def update_interface(iid: int, req: InterfaceReq, payload: dict = Depends(require_perm("system_config"))):
    from src.quant_common.crypto import encrypt
    caps, exchanges = _validate_iface(req.provider, req.market, req.exchanges, req.capabilities)
    params = _normalize_params(req.params)
    if req.credentials:   # 空=不改（三段语义），非空才校验 secret 字段
        _validate_credentials(req.provider, req.credentials)
    enc = encrypt(req.credentials) if req.credentials else None   # 空=不改（三段语义）
    with get_conn() as conn:
        cur = conn.execute("SELECT capabilities FROM external_interface WHERE id=%s", (iid,))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "IFACE_NOT_FOUND", "接口不存在")
        old_domain_trading = "trading" in list(r[0] or [])
        # 盲审 B-P1：改能力致换域=行种变更（position 残留旧域+消费方选行被遮蔽）——拒，删了重建
        if old_domain_trading != ("trading" in caps):
            raise ApiError(400, "IFACE_DOMAIN_CHANGE",
                           "本次修改会使行在数据域/交易域间切换（position 与选行序语义破坏）——请删除后按新行种重建")
        try:
            if enc is not None:
                conn.execute(
                    "UPDATE external_interface SET name=%s, provider=%s, market=%s, exchanges=%s, "
                    "credentials_encrypted=%s, params=%s::jsonb, capabilities=%s, enabled=%s, account_key=%s, updated_at=now() "
                    "WHERE id=%s",
                    (req.name, req.provider, req.market, exchanges, enc,
                     json.dumps(params, ensure_ascii=False), caps, req.enabled, req.account_key, iid))
            else:
                conn.execute(
                    "UPDATE external_interface SET name=%s, provider=%s, market=%s, exchanges=%s, "
                    "params=%s::jsonb, capabilities=%s, enabled=%s, account_key=%s, updated_at=now() WHERE id=%s",
                    (req.name, req.provider, req.market, exchanges,
                     json.dumps(params, ensure_ascii=False), caps, req.enabled, req.account_key, iid))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if _is_unique_violation(e):
                raise ApiError(409, "IFACE_ACCOUNT_KEY_DUP",
                               f"account_key {req.account_key!r} 已被 provider {req.provider} 占用")
            raise
    try:
        from src.data_platform.routing import bump_config_version
        bump_config_version()   # 批 57：接口行变更 bump（28 §6.3——position/enabled 改动 epoch 生效）
    except Exception:
        pass
    audit_log(payload["username"], "interface_update", f"id={iid}")
    return {"ok": True}


@router.delete("/api/interfaces/{iid}")
def delete_interface(iid: int, payload: dict = Depends(require_perm("system_config"))):
    """删除外部接口行。D2：venue 被 live_task 引用时 FK RESTRICT 拒绝（防删实盘任务账号）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id FROM live_task WHERE venue_id=%s LIMIT 1", (iid,))
        if cur.fetchone() is not None:
            raise ApiError(409, "IFACE_IN_USE",
                           "该交易账号下存在实盘任务，禁止删除（请先停止并删除相关任务）")
        try:
            conn.execute("DELETE FROM external_interface WHERE id=%s", (iid,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if _is_fk_violation(e):
                raise ApiError(409, "IFACE_IN_USE",
                               "该接口被其他记录引用（外键约束），禁止删除")
            raise
    audit_log(payload["username"], "interface_delete", f"id={iid}")
    return {"ok": True}


@router.post("/api/interfaces/{iid}/test")
def test_interface(iid: int, payload: dict = Depends(require_perm("read"))):
    """连接测试：按能力路由注册表（trading∈caps 或 provider∈Broker 注册表→Broker；
    否则 DataSource）——盲审 A-P2：quote-only 交易 provider 行也须测得了。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT provider, credentials_encrypted, params, capabilities "
            "FROM external_interface WHERE id=%s", (iid,))
        r = cur.fetchone()
    if not r:
        return {"ok": False, "error": "接口不存在"}
    provider, caps = r[0], list(r[3])
    params_str = json.dumps(r[2]) if isinstance(r[2], dict) else r[2]
    from src.strategy_framework.broker import _REGISTRY as _broker_reg
    if "trading" in caps or provider in _broker_reg:
        cls = _broker_reg.get(provider)
        fail_msg = "凭证不完整或连接失败（真连 vnpy 在服务器）"
    else:
        from src.data_platform.data_source import _REGISTRY as _ds_reg
        cls = _ds_reg.get(provider)
        fail_msg = "连接测试失败，看日志"
    if not cls:
        return {"ok": False, "error": f"provider {provider} 未注册（需实现相应子类）"}
    obj = cls(credentials_encrypted=r[1], params=params_str)
    ok = obj.test_connection()
    return {"ok": ok, "error": "" if ok else fail_msg}


# --- 积分档四层限流（points_tier 预设 / rate_limits 覆写 / 熔断参数，2026-08-27） ---


def _ds_cls(provider: str):
    """provider → 已注册 DataSource 类（未注册 404）。"""
    from src.data_platform.data_source import _REGISTRY
    cls = _REGISTRY.get(provider)
    if not cls:
        raise ApiError(404, "DS_NOT_REGISTERED", f"provider {provider} 未注册（需实现 DataSource 子类）")
    return cls


def _load_ds_params(provider: str) -> tuple[int, dict]:
    """读 provider 数据域配置行（enabled 优先，域内 position 序）→ (id, params dict)；无配置 404。

    批55a：data_source_config→external_interface（终裁三消费方①——provider+position 域内序）。
    """
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT id, params FROM external_interface WHERE provider=%s AND {_DOMAIN_DATA} "
            "ORDER BY enabled DESC, position, id LIMIT 1", (provider,))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "DS_NOT_FOUND", f"数据源 {provider} 无配置")
    if isinstance(r[1], dict):
        return r[0], r[1]          # params=jsonb（psycopg 读侧已 dict）
    try:
        return r[0], (json.loads(r[1]) if r[1] else {})
    except (TypeError, ValueError):
        logger.warning("external_interface(%s) params 非法 JSON，按空处理", provider)
        return r[0], {}


def _save_ds_params(dsid: int, params: dict) -> None:
    """params 整体写回（读-改-写）。双盲补审修正：有 last-writer-wins 窗口
    （两 admin 并发、或 cb 与 rate_limits 两端点并发丢一边修改）——admin 低频可接受，
    根治需 SELECT FOR UPDATE 同事务。
    """
    with get_conn() as conn:
        conn.execute("UPDATE external_interface SET params=%s::jsonb, updated_at=now() WHERE id=%s",
                     (json.dumps(params, ensure_ascii=False), dsid))
        conn.commit()


@router.get("/api/datasource/capabilities")
def get_capabilities(payload: dict = Depends(require_perm("read"))):
    """各数据源 adapter 的能力矩阵（24 号供给矩阵数据源：provider → 支持的 sync_id 列表）。"""
    from src.data_platform.adapters.base import _ADAPTERS
    return {"providers": {
        provider: sorted(cls.capabilities)
        for provider, cls in _ADAPTERS.items()
    }}


@router.get("/api/datasource/{provider}/rate-limits")
def get_rate_limits(provider: str,
                    payload: dict = Depends(require_perm("read"))):
    """限速覆写 + 熔断参数（24 号限速抽象聚合：去积分档，限速=类默认 + DB 覆写两级）。"""
    cls = _ds_cls(provider)
    _, params = _load_ds_params(provider)
    overrides = params.get("rate_limits") or {}
    ds = cls(params=json.dumps(params))
    apis = []
    for name in sorted(set(cls.DEFAULT_RATE_LIMITS) | set(overrides)):
        apis.append({
            "api": name,
            "default": float(cls.DEFAULT_RATE_LIMITS.get(name, 0.0)),   # 类默认
            "override": overrides.get(name),                            # DB 覆写（None=未覆写）
            "effective": ds.get_rate_limit(name),                       # 当前生效值
        })
    return {
        "provider": provider,
        "apis": apis,
        "circuit_breaker": {
            "fail_threshold": ds.get_param_float(
                "circuit_breaker", "fail_threshold", default=5.0, lo=1.0, hi=1000.0),
            "reset_timeout": ds.get_param_float(
                "circuit_breaker", "reset_timeout", default=60.0, lo=1.0, hi=86400.0),
        },
    }


@router.post("/api/datasource/{provider}/rate-limit-override")
def set_rate_limit_override(provider: str, req: RateLimitOverrideReq,
                            payload: dict = Depends(require_perm("system_config"))):
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
                   payload: dict = Depends(require_perm("read"))):
    from src.task_manager import list_tasks
    return {"items": list_tasks(status=status, limit=limit)}


@router.get("/api/tasks/{task_id}")
def get_task_api(task_id: str,
                 payload: dict = Depends(require_perm("read"))):
    from src.task_manager import get_task
    t = get_task(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


@router.post("/api/tasks/{task_id}/terminate")
def terminate_task_api(task_id: str,
                       payload: dict = Depends(require_perm("trade"))):
    from src.task_manager import terminate_task, log_task
    terminate_task(task_id)
    log_task(task_id, "WARN", f"用户 {payload['username']} 终止任务")
    audit_log(payload["username"], "task_terminate", task_id)
    return {"ok": True}


@router.post("/api/tasks/{task_id}/force-delete")
def force_delete_task_api(task_id: str,
                          payload: dict = Depends(require_perm("system_config"))):
    from src.task_manager import force_delete_task
    force_delete_task(task_id)
    audit_log(payload["username"], "task_force_delete", task_id)
    return {"ok": True}


@router.post("/api/tasks/detect-stuck")
def detect_stuck_api(payload: dict = Depends(require_perm("system_config"))):
    from src.task_manager import detect_stuck
    count = detect_stuck()
    return {"stuck_count": count}


# --- venue_permission（D1：venue 侧品种权限——权限人工配置，读/写侧） ---

_VALID_CATEGORIES = {"stock", "etf", "convertible", "fund", "reits", "perp"}
_VALID_EXCHANGES = {"SHSE", "SZSE", "BSE", "BINANCE", "OKX"}
_VALID_BOARDS = {"main", "star", "chinext", "bse"}
_VENUE_PERM_COLS = ("venue_id, allowed_categories, allowed_exchanges, allowed_boards, "
                    "is_st_allowed, convertible_allowed")


def _venue_perm_row(r) -> dict:
    return {"venue_id": r[0], "allowed_categories": list(r[1]), "allowed_exchanges": list(r[2]),
            "allowed_boards": list(r[3]), "is_st_allowed": r[4], "convertible_allowed": r[5]}


@router.get("/api/venues/{venue_id}/permission")
def get_venue_permission(venue_id: int, payload: dict = Depends(require_perm("read"))):
    """venue 权限读（无行 404——权限人工配置，未配置不猜默认）。"""
    with get_conn() as conn:
        cur = conn.execute(f"SELECT {_VENUE_PERM_COLS} FROM venue_permission WHERE venue_id=%s", (venue_id,))
        r = cur.fetchone()
    if r is None:
        raise ApiError(404, "VENUE_PERM_NOT_FOUND", f"venue {venue_id} 未配置权限")
    return _venue_perm_row(r)


@router.put("/api/venues/{venue_id}/permission")
def put_venue_permission(venue_id: int, req: VenuePermissionReq, payload: dict = Depends(require_perm("system_config"))):
    """venue 权限写（upsert）。校验：venue 存在且交易域；值域 ⊆ 注册表（防拼写错）。"""
    bad_cats = set(req.allowed_categories) - _VALID_CATEGORIES
    bad_exch = set(req.allowed_exchanges) - _VALID_EXCHANGES
    bad_brds = set(req.allowed_boards) - _VALID_BOARDS
    if bad_cats or bad_exch or bad_brds:
        raise ApiError(400, "VENUE_PERM_BAD_VALUE",
                       f"非法值：category={sorted(bad_cats)} exchange={sorted(bad_exch)} board={sorted(bad_brds)}")
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id FROM external_interface WHERE id=%s AND 'trading' = ANY(capabilities)", (venue_id,))
        if cur.fetchone() is None:
            raise ApiError(404, "VENUE_NOT_FOUND", f"venue {venue_id} 不存在或非交易域")
        cur = conn.execute(
            "INSERT INTO venue_permission (venue_id, allowed_categories, allowed_exchanges, "
            "allowed_boards, is_st_allowed, convertible_allowed) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (venue_id) DO UPDATE SET "
            "allowed_categories=EXCLUDED.allowed_categories, "
            "allowed_exchanges=EXCLUDED.allowed_exchanges, "
            "allowed_boards=EXCLUDED.allowed_boards, "
            "is_st_allowed=EXCLUDED.is_st_allowed, "
            "convertible_allowed=EXCLUDED.convertible_allowed, updated_at=now()",
            (venue_id, list(req.allowed_categories), list(req.allowed_exchanges),
             list(req.allowed_boards), req.is_st_allowed, req.convertible_allowed))
        conn.commit()
    audit_log(payload["username"], "venue_permission_update", f"venue={venue_id}")
    return {"venue_id": venue_id}


