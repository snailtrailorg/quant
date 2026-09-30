"""平台化管理路由：配置面两族（数据源/交易账号）/限流四层/任务 —— 从 main.py 提取的 mgmt 端点。

批55a：data_source_config+broker_config 合并为 external_interface（行=账号/列=能力/页签=过滤视图）。
批55b：旧端点垫片已随前端切换删除（批39 channels 同款）。
**批 83a 拆表**（2026-09-29 用户裁定·抽象判据——无继承不揉合）：external_interface 拆
`data_source`（拉取侧：token/rate_limits/熔断，无 exchanges/account_key）+ `trading_account`
（下单/行情侧：exchanges/account_key/连接参数）。CRUD 拆两族端点（/api/data-sources 与
/api/trading-accounts）——**域=表=端点族=列集=能力集**五者同源（字面量真源 markets.DOMAIN_*）：
原「单列表+能力筛选」可枚举出的揉合行（如「交易行挂 hist_quote」）不再可能被建出。

两族端点同构（7 端点 × 2），实现共用 `_list/_providers/_create/_reorder/_update/_delete/_test`
（kind 参数分叉列集），端点本体只是路径 + 权限门。

错误码沿用历史 `IFACE_*` 前缀（前端/运维手册已引；换前缀=纯改名无收益）；新增域维两码
`IFACE_CAP_DOMAIN`（越域能力）/`IFACE_DOMAIN_MISMATCH`（provider 与端点族错域）。
注意 `/api/datasource/*`（单数）是无关的限流/能力矩阵族，勿与 `/api/data-sources` 混。
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from src.data_platform import config_store

from ..auth import audit_log, require_perm
from ..errors import ApiError
from ..models import (AccountPermissionReq, DataSourceReq, InterfaceReorderReq,
                      RateLimitOverrideReq, TradingAccountReq)

logger = logging.getLogger("web_api")

router = APIRouter(tags=["mgmt"])

# --- 配置面两族（批 83a：域=表——端点族/表名/列集/能力集同源，字面量只有 markets.DOMAIN_*） ---

KIND_DATA = config_store.KIND_DATA          # 数据源（拉取侧）
KIND_TRADING = config_store.KIND_TRADING    # 交易账号（下单/行情侧）
_KIND_LABEL = config_store.LABEL
# 每域能力集真源=markets.DOMAIN_CAPS，经 capabilities.domain_capabilities 消费（域逻辑在数据层）；
# 收窄立法：写侧 ⊆ 本域集——合表时代可建出混合行，拆表后不可能。
# 两族数据面 SQL 全部在 data_platform/config_store.py（层 1）——本文件 SQL 计数为 0。


def _default_exchanges(provider: str) -> list[str] | None:
    """perp 类 provider 的默认单所覆盖（盲审 B-P2：不写 exchanges 则 NULL=全所语义错——
    binance_perp 的 NULL 会含 OKX）。推导源=MARKET_OP_DECOMP 的 exchange；其余 None=全所。"""
    from src.quant_common.markets import MARKET_OP_DECOMP
    d = MARKET_OP_DECOMP.get(provider)
    return [d[2]] if d and d[2] else None


def _validate_iface(provider: str, market: str, capabilities, kind: str) -> list:
    """写侧校验（方案一 v2 六必修 + 83a 域收窄）：market∈MARKETS 且=PROVIDER_MARKET[provider]；
    provider 归属域=本端点族；capabilities 非空、⊆代码能力、且 ⊆本域能力集（越集/错域 400）。
    返回规范化 caps。exchanges 校验见 _validate_exchanges（仅交易族调用）。
    """
    from src.data_platform.capabilities import (check_capability_domain,
                                                check_capability_subset, provider_domain)
    from src.quant_common.markets import MARKETS, PROVIDER_MARKET
    if market not in MARKETS:
        raise ApiError(400, "IFACE_MARKET_UNKNOWN", f"未知市场 {market}（注册表：{sorted(MARKETS)}）")
    expect = PROVIDER_MARKET.get(provider)
    if expect is None:
        raise ApiError(400, "IFACE_PROVIDER_UNKNOWN",
                       f"provider {provider} 未注册（需先实现接入并登记 markets.PROVIDER_MARKET）")
    if market != expect:
        raise ApiError(400, "IFACE_MARKET_MISMATCH",
                       f"provider {provider} 归属市场 {expect}，与提交 {market} 不符")
    # 83a：provider 归属域须与本端点族一致（防「数据源端点建 XTP 行」这类跨域错行）。
    # 跨域/空能力 provider（provider_domain=None）不在此拦——由下方 ⊆代码能力 兜底。
    dom = provider_domain(provider)
    if dom is not None and dom != kind:
        raise ApiError(400, "IFACE_DOMAIN_MISMATCH",
                       f"provider {provider} 属于「{_KIND_LABEL.get(dom, dom)}」域，"
                       f"不能经「{_KIND_LABEL[kind]}」端点配置")
    caps = set(capabilities or [])
    if not caps:
        raise ApiError(400, "IFACE_CAP_EMPTY", "capabilities 不能为空（至少启用一项能力）")
    ok, msg = check_capability_subset(provider, caps)
    if not ok:
        raise ApiError(400, "IFACE_CAP_EXCESS", msg)
    ok, msg = check_capability_domain(kind, caps)
    if not ok:
        raise ApiError(400, "IFACE_CAP_DOMAIN", msg)
    return sorted(caps)


def _validate_exchanges(provider: str, market: str, exchanges) -> list | None:
    """交易族 exchanges 校验（含 perp 单所覆盖约束）。返回规范化值（None=全所）。"""
    from src.quant_common.markets import EXCHANGES
    if exchanges is None:
        exchanges = _default_exchanges(provider)   # perp 缺省=单所（防 NULL 全所语义错）
    if exchanges:
        market_ex = {e for e, v in EXCHANGES.items() if v["market"] == market}
        bad = set(exchanges) - market_ex
        if bad:
            raise ApiError(400, "IFACE_EXCHANGE_UNKNOWN",
                           f"交易所 {sorted(bad)} 不属于市场 {market}（该市场全所：{sorted(market_ex)}）")
        # 盲审 B-P1-4：perp 类 provider 只能覆盖自身 exchange（防 binance 通道勾 OKX 的语义错行）
        from src.quant_common.markets import MARKET_OP_DECOMP
        d = MARKET_OP_DECOMP.get(provider)
        if d and d[2]:
            bad = set(exchanges) - {d[2]}
            if bad:
                raise ApiError(400, "IFACE_EXCHANGE_UNKNOWN",
                               f"provider {provider} 仅覆盖 {[d[2]]}（不可勾 {sorted(bad)}）")
    return list(exchanges) if exchanges else None


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


# --- 两族共用的实现（端点本体只做路径 + 权限门，逻辑在此——kind 分叉列集） ---


def _list(kind: str, cap: str | None = None) -> list[dict]:
    """本域行列表（?cap=能力筛选，值域=本域能力集——跨域/旧词深链 400 而非静默空表）。

    附漂移告警（55-0 共同底座：配置能力不在代码能力集=注册表防漂移闸同模式，批33b 先例）。
    """
    from src.data_platform.capabilities import check_capability_subset, domain_capabilities
    if cap and cap not in domain_capabilities(kind):   # 盲审 B-P2-6：值域校验（防旧词/跨域深链静默空表）
        raise ApiError(400, "IFACE_CAP_INVALID",
                       f"cap 须为 {sorted(domain_capabilities(kind))} 之一（{_KIND_LABEL[kind]}域）")
    items = []
    for r in config_store.list_rows(kind, cap):
        d = config_store.row_dict(r, kind)
        ok, msg = check_capability_subset(d["provider"], set(d["capabilities"]))
        if not ok:
            logger.warning("%s %s(id=%s) 能力漂移：%s", _KIND_LABEL[kind], d["provider"], d["id"], msg)
        items.append(d)
    return items


def _providers(kind: str) -> dict:
    """本域 provider 目录（55b：新建弹窗下拉零硬编码——注册表派生 + 83a 域过滤）。

    provider→{market, capabilities=代码能力全集}；**域外 provider 与空能力 stub 源不出目录**
    （前者本端点族必拒，后者写侧 ⊆ 校验建不了行——列出来只会引导用户撞 400）。
    83a：随目录回 `domain_capabilities`=本域能力集（前端能力筛选/勾选零字面量，与写侧
    `check_capability_domain` 同源 `markets.DOMAIN_CAPS`——前端不再镜像 token 表）。
    """
    from src.data_platform.capabilities import (domain_capabilities, provider_capabilities,
                                                provider_domain)
    from src.data_platform.interfaces import list_interface_schemas
    from src.quant_common.markets import EXCHANGES, PROVIDER_MARKET
    schemas = list_interface_schemas()
    out = []
    for p, m in sorted(PROVIDER_MARKET.items()):
        if provider_domain(p) != kind:
            continue
        caps = provider_capabilities(p)
        if not caps:
            continue
        out.append(
            {"provider": p, "market": m, "capabilities": sorted(caps),
             "market_exchanges": sorted(e for e, v in EXCHANGES.items() if v["market"] == m),
             "default_exchanges": _default_exchanges(p),   # 批55b 盲审修：perp 预填单所（防"不选=全部"文案与后端钉默认不一致）
             "field_schema": schemas.get(p, {}).get("field_schema", []),      # 批63：凭证字段 schema（前端动态表单）
             "params_schema": schemas.get(p, {}).get("params_schema", [])})   # 批63：参数字段 schema
    return {"providers": out, "domain_capabilities": sorted(domain_capabilities(kind))}


def _write_values(kind: str, req, caps: list) -> dict:
    """请求 → 写入列 dict（列名白名单在 config_store；credentials 空=键缺席=不改，三段语义）。

    `params` 传 **dict**（`_normalize_params` 已把 HTTP 边界的 str/dict 归一为 dict，
    原在此 `json.dumps` 又被 config_store 收成字符串——2026-09-30 裁定契约统一后删除该
    白跑一趟：序列化归 `config_store._param_jsonb` 单出口，且字符串入参会响亮拒绝）。
    """
    from src.quant_common.crypto import encrypt
    values = {"name": req.name, "provider": req.provider, "market": req.market,
              "params": _normalize_params(req.params),
              "capabilities": caps, "enabled": req.enabled}
    if kind == KIND_TRADING:
        values["exchanges"] = _validate_exchanges(req.provider, req.market, req.exchanges)
        values["account_key"] = req.account_key
    if req.credentials:
        values["credentials_encrypted"] = encrypt(req.credentials)
    return values


def _create(kind: str, req, payload: dict) -> dict:
    """新增本域行。position=**本表** max+1（批 83a：单表单序列——原全局单序列随拆表退役，
    两表各一条序列，互不干扰；合表时代域内 max 与全局重编号撞号的问题一并消失）。"""
    caps = _validate_iface(req.provider, req.market, req.capabilities, kind)
    _validate_credentials(req.provider, req.credentials)   # 批63：secret 字段必填（对标 IM）
    values = _write_values(kind, req, caps)   # 凭证空=键缺席→INSERT 该列 NULL（新建无凭证）
    try:
        new_id = config_store.insert_row(kind, values)
    except Exception as e:
        if config_store.is_unique_violation(e):
            raise ApiError(409, "IFACE_ACCOUNT_KEY_DUP",
                           f"account_key {req.account_key!r} 已被 provider {req.provider} 占用")
        raise
    audit_log(payload["username"], f"{kind}_create", f"{req.provider} caps={caps}")
    return {"id": new_id}


def _reorder(kind: str, req, payload: dict) -> dict:
    """本表拖拽重排（批 83a：**单表单序列**——body={ids} 为本表全量有序数组）。

    全量校验（防并发丢行/幽灵 id）；幂等；并发=后写赢（低频管理操作）。
    前端筛选态禁拖（规避筛选子集触发全量校验 400）。
    """
    if not req.ids or not all(isinstance(i, int) for i in req.ids):
        raise ApiError(400, "BAD_PARAM", "ids 须为非空整数数组")
    existing = config_store.all_ids(kind)
    if set(req.ids) != existing or len(req.ids) != len(existing):
        raise ApiError(400, "BAD_PARAM",
                       f"ids 必须等于当前全部{_KIND_LABEL[kind]}行 id（本表全量序列——防并发丢行）")
    config_store.set_positions(kind, list(req.ids))
    try:
        from src.data_platform.routing import bump_config_version
        bump_config_version()   # 批 57：position 改动 bump（28 §6.3——epoch 生效验收②闭环）
    except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
        pass
    audit_log(payload["username"], f"{kind}_reorder", detail=f"order={req.ids}")
    return {"ok": True}


def _update(kind: str, iid: int, req, payload: dict) -> dict:
    """改本域行。credentials 空=不改（三段语义）。

    **域锁退役**：合表时代「改能力致换域」须拒（IFACE_DOMAIN_CHANGE，position/选行序语义破坏）；
    拆表后域由端点族钉死（能力集 ⊆ 本域），换域只能删了在另一族重建——该校验自然消失。
    """
    caps = _validate_iface(req.provider, req.market, req.capabilities, kind)
    if req.credentials:   # 空=不改（三段语义），非空才校验 secret 字段
        _validate_credentials(req.provider, req.credentials)
    if not config_store.row_exists(kind, iid):
        raise ApiError(404, "IFACE_NOT_FOUND", f"{_KIND_LABEL[kind]}行不存在")
    values = _write_values(kind, req, caps)   # 凭证空=键缺席=不改（_WRITE_COLS 子集语义）
    try:
        config_store.update_row(kind, iid, values)
    except Exception as e:
        if config_store.is_unique_violation(e):
            raise ApiError(409, "IFACE_ACCOUNT_KEY_DUP",
                           f"account_key {req.account_key!r} 已被 provider {req.provider} 占用")
        raise
    try:
        from src.data_platform.routing import bump_config_version
        bump_config_version()   # 批 57：接口行变更 bump（28 §6.3——position/enabled 改动 epoch 生效）
    except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
        pass
    audit_log(payload["username"], f"{kind}_update", f"id={iid}")
    return {"ok": True}


def _delete(kind: str, iid: int, payload: dict) -> dict:
    """删本域行。交易族：account 被 live_task 引用时 FK RESTRICT 拒绝（防删实盘任务账号）。

    **预检只对交易族**（83a 拆表踩点）：live_task.account_id 指向 trading_account.id；
    两表 id 各自独立，数据源行 id 可能与某 live_task.account_id 数值撞车——若不分域预检，
    删数据源行会被误判「该交易账号下有实盘任务」。数据源族仅靠 FK 兜底（本无 FK 指向它）。
    """
    if kind == KIND_TRADING and config_store.has_live_task(iid):
        raise ApiError(409, "IFACE_IN_USE",
                       "该交易账号下存在实盘任务，禁止删除（请先停止并删除相关任务）")
    try:
        config_store.delete_row(kind, iid)
    except Exception as e:
        if config_store.is_fk_violation(e):
            raise ApiError(409, "IFACE_IN_USE",
                           "该行被其他记录引用（外键约束），禁止删除")
        raise
    audit_log(payload["username"], f"{kind}_delete", f"id={iid}")
    return {"ok": True}


def _test(kind: str, iid: int) -> dict:
    """连接测试：按族路由注册表（交易族→Broker 注册表，回落 DataSource 以覆盖纯行情通道行；
    数据源族→DataSource 注册表）——盲审 A-P2：quote-only 行也须测得了。"""
    r = config_store.get_conn_row(kind, iid)
    if not r:
        return {"ok": False, "error": f"{_KIND_LABEL[kind]}行不存在"}
    provider = r[0]
    params_str = json.dumps(r[2]) if isinstance(r[2], dict) else r[2]
    from src.data_platform.data_source import _REGISTRY as _ds_reg
    if kind == KIND_TRADING:
        from src.strategy_framework.broker import _REGISTRY as _broker_reg
        cls = _broker_reg.get(provider) or _ds_reg.get(provider)
        fail_msg = "凭证不完整或连接失败（真连 vnpy 在服务器）"
    else:
        cls = _ds_reg.get(provider)
        fail_msg = "连接测试失败，看日志"
    if not cls:
        return {"ok": False, "error": f"provider {provider} 未注册（需实现相应子类）"}
    obj = cls(credentials_encrypted=r[1], params=params_str)
    ok = obj.test_connection()
    return {"ok": ok, "error": "" if ok else fail_msg}


# --- 数据源族（/api/data-sources）---
# 注意：本族与无关的单数 `/api/datasource/*`（能力矩阵/限流覆写）不同名，勿混。


@router.get("/api/data-sources")
def list_data_sources(cap: str | None = None, payload: dict = Depends(require_perm("read"))):
    """数据源列表（?cap=能力筛选，值域 hist_quote|ref_data|inst_event）。"""
    return _list(KIND_DATA, cap)


@router.get("/api/data-sources/providers")   # 静态路由前移防 {iid} 遮蔽（批43 P0-3 同款）
def list_data_source_providers(payload: dict = Depends(require_perm("read"))):
    """数据源 provider 目录（域外 provider 与空能力 stub 源不出目录）。"""
    return _providers(KIND_DATA)


@router.post("/api/data-sources")
def create_data_source(req: DataSourceReq, payload: dict = Depends(require_perm("system_config"))):
    """新增数据源行（position=本表 max+1）。"""
    return _create(KIND_DATA, req, payload)


@router.post("/api/data-sources/reorder")   # 路由前移防 {iid} int 遮蔽 422（批43 P0-3 教训）
def data_sources_reorder(req: InterfaceReorderReq,
                         payload: dict = Depends(require_perm("system_config"))):
    """数据源表内拖拽重排（单表单序列——ids=本表全部行 id）。"""
    return _reorder(KIND_DATA, req, payload)


@router.post("/api/data-sources/{iid}")
def update_data_source(iid: int, req: DataSourceReq,
                       payload: dict = Depends(require_perm("system_config"))):
    """改数据源行（credentials 空=不改）。"""
    return _update(KIND_DATA, iid, req, payload)


@router.delete("/api/data-sources/{iid}")
def delete_data_source(iid: int, payload: dict = Depends(require_perm("system_config"))):
    """删数据源行（无 FK 指向本表，仅兜底映射）。"""
    return _delete(KIND_DATA, iid, payload)


@router.post("/api/data-sources/{iid}/test")
def test_data_source(iid: int, payload: dict = Depends(require_perm("read"))):
    """数据源连接测试（DataSource 注册表）。"""
    return _test(KIND_DATA, iid)


# --- 交易账号族（/api/trading-accounts）---


@router.get("/api/trading-accounts")
def list_trading_accounts(cap: str | None = None, payload: dict = Depends(require_perm("read"))):
    """交易账号列表（?cap=能力筛选，值域 rt_quote|trading）。"""
    return _list(KIND_TRADING, cap)


@router.get("/api/trading-accounts/providers")   # 静态路由前移防 {iid} 遮蔽
def list_trading_account_providers(payload: dict = Depends(require_perm("read"))):
    """交易账号 provider 目录（域外 provider 与空能力 stub 源不出目录）。"""
    return _providers(KIND_TRADING)


@router.post("/api/trading-accounts")
def create_trading_account(req: TradingAccountReq,
                           payload: dict = Depends(require_perm("system_config"))):
    """新增交易账号行（position=本表 max+1）。"""
    return _create(KIND_TRADING, req, payload)


@router.post("/api/trading-accounts/reorder")   # 路由前移防 {iid} int 遮蔽 422
def trading_accounts_reorder(req: InterfaceReorderReq,
                             payload: dict = Depends(require_perm("system_config"))):
    """交易账号表内拖拽重排（单表单序列——ids=本表全部行 id）。"""
    return _reorder(KIND_TRADING, req, payload)


@router.post("/api/trading-accounts/{iid}")
def update_trading_account(iid: int, req: TradingAccountReq,
                           payload: dict = Depends(require_perm("system_config"))):
    """改交易账号行（credentials 空=不改）。"""
    return _update(KIND_TRADING, iid, req, payload)


@router.delete("/api/trading-accounts/{iid}")
def delete_trading_account(iid: int, payload: dict = Depends(require_perm("system_config"))):
    """删交易账号行。D2：account 被 live_task 引用时 FK RESTRICT 拒绝（防删实盘任务账号）。"""
    return _delete(KIND_TRADING, iid, payload)


@router.post("/api/trading-accounts/{iid}/test")
def test_trading_account(iid: int, payload: dict = Depends(require_perm("read"))):
    """交易账号连接测试（Broker 注册表，回落 DataSource）。"""
    return _test(KIND_TRADING, iid)


# --- 积分档四层限流（points_tier 预设 / rate_limits 覆写 / 熔断参数，2026-08-27） ---


def _ds_cls(provider: str):
    """provider → 已注册 DataSource 类（未注册 404）。"""
    from src.data_platform.data_source import _REGISTRY
    cls = _REGISTRY.get(provider)
    if not cls:
        raise ApiError(404, "DS_NOT_REGISTERED", f"provider {provider} 未注册（需实现 DataSource 子类）")
    return cls


def _load_ds_params(provider: str) -> tuple[int, dict]:
    """读 provider 数据源配置行 → (id, params dict)；无配置 404（限流四层三消费方①）。

    批 83a：读表下沉 config_store（层 1）——本函数只做「无行 → 404」的错误码映射。
    """
    r = config_store.load_provider_params(provider)
    if r is None:
        raise ApiError(404, "DS_NOT_FOUND", f"数据源 {provider} 无配置")
    return r


def _save_ds_params(dsid: int, params: dict) -> None:
    """params 整体写回（读-改-写）——写路径下沉 config_store。"""
    config_store.save_provider_params(dsid, params)


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
    if req.pacer is not None:   # 批 73：provider 总闸（params.pacer.min_interval——0=关/默认）
        if not 0 <= req.pacer <= 30:
            raise ApiError(400, "PACER_VALUE_INVALID", "总闸间隔须在 [0, 30] 秒（等待上限对齐）")
        params = {**params, "pacer": {"min_interval": req.pacer}}
        _save_ds_params(dsid, params)
        audit_log(payload["username"], "data_source_pacer_update", f"{provider} min_interval={req.pacer}s")
        return {"ok": True, "pacer": {"min_interval": req.pacer}}
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
    from src.task_manager import log_task, terminate_task
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


# --- account_permission（D1：account 侧品种权限——权限人工配置，读/写侧） ---

_VALID_CATEGORIES = {"stock", "etf", "convertible", "fund", "reits", "perp"}
_VALID_EXCHANGES = {"SHSE", "SZSE", "BSE", "BINANCE", "OKX"}
_VALID_BOARDS = {"main", "star", "chinext", "bse"}


def _account_perm_row(r) -> dict:
    return {"account_id": r[0], "allowed_categories": list(r[1]), "allowed_exchanges": list(r[2]),
            "allowed_boards": list(r[3]), "is_st_allowed": r[4], "convertible_allowed": r[5]}


@router.get("/api/accounts/{account_id}/permission")
def get_account_permission(account_id: int, payload: dict = Depends(require_perm("read"))):
    """account 权限读（无行 404——权限人工配置，未配置不猜默认）。"""
    r = config_store.get_account_permission(account_id)
    if r is None:
        raise ApiError(404, "ACCOUNT_PERM_NOT_FOUND", f"account {account_id} 未配置权限")
    return _account_perm_row(r)


@router.put("/api/accounts/{account_id}/permission")
def put_account_permission(account_id: int, req: AccountPermissionReq, payload: dict = Depends(require_perm("system_config"))):
    """account 权限写（upsert）。校验：account 存在且为交易账号；值域 ⊆ 注册表（防拼写错）。

    批 83a：存在性判据从「external_interface 交易域行」改为「trading_account 行」
    （表本身即交易域，域谓词退役）。
    """
    bad_cats = set(req.allowed_categories) - _VALID_CATEGORIES
    bad_exch = set(req.allowed_exchanges) - _VALID_EXCHANGES
    bad_brds = set(req.allowed_boards) - _VALID_BOARDS
    if bad_cats or bad_exch or bad_brds:
        raise ApiError(400, "ACCOUNT_PERM_BAD_VALUE",
                       f"非法值：category={sorted(bad_cats)} exchange={sorted(bad_exch)} board={sorted(bad_brds)}")
    if not config_store.row_exists(KIND_TRADING, account_id):
        raise ApiError(404, "ACCOUNT_NOT_FOUND", f"account {account_id} 不存在或非交易账号")
    config_store.upsert_account_permission(
        account_id, req.allowed_categories, req.allowed_exchanges, req.allowed_boards,
        req.is_st_allowed, req.convertible_allowed)
    audit_log(payload["username"], "account_permission_update", f"account={account_id}")
    return {"account_id": account_id}



