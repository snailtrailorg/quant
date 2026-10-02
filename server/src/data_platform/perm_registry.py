"""批33b：权限资源注册表（唯一字面量层）——api 键/nav 条目/market 键/路由别名四清单单源。

收编前的三份平行清单（勘察病根）：auth_routes.NAV_ITEMS 17 / auth_routes.all_keys 14（+
perms.PERMISSIONS admin 集第二份）/ perms._MARKET_OP_KEYS 5（已单源）+前端
ROUTE_TO_NAV 10 别名（MainLayout 硬编码）。本模块成为唯一字面量层，perms.py 单向
import（禁反向——循环 import）；DB 覆盖层 perm_resource 表（迁移 0084）只改显示
四字段（group/order/label/enabled），条目集恒代码单源（阶段二红线：不可增删）。

载入容错（B-P1-3，perms.py 先例）：读失败回退代码底座+失败结果不缓存；注册表显示
缓存与 _PERM_CACHE（鉴权）两套独立互不失效（A-P1-8）。

绑定扫描（A-P0-1）：FastAPI 0.141.1 include_router 懒加载——app.routes 是
_IncludedRouter 占位（实测 0 APIRoute），必须扫 APIRouter 实例（sys.modules 收集）。
"""
from __future__ import annotations

import logging
import time

_logger = logging.getLogger("data_platform.perm_registry")

# ——— 代码底座（唯一字面量层） ———

# api 权限键 15（顺序=原 all_keys 声明序——GET /permissions 供形保序等价）
# 批 77 增 `live_control`（实盘面：起停实盘任务/策略进程、解冻）——从 `strategy_control` 拆出。
# 起因：`strategy_control` 一键两域（研究面=写策略/因子/回测；实盘面=起 systemd 进程/解冻），
# analyst 持该键即越界（「analyst 只能回测与实盘测试，不执行实盘交易」）。权限矩阵见
# `flow/任务/批77-权限矩阵与实盘面分离.md` §四。
# 批 86-B 增 `paper_trade`（纸上交易面）——**新增的第三个维**：
#   跑**纸上任务**（paper mode：实时数据 + 完整风控 + 不下单）的能力。
#   同时承载批 77 那句「analyst 只能回测和实盘测试」里的「实盘测试」职能（见下）。
# 命名依据（业界规范，非自造词）：`flow/任务/批86B-命名裁决.md`。原拟名 `live_test` 已废
# （`test` 无出处；且「实盘」修饰一个不下单的东西自相矛盾）。
#
# ⚠️ **曾拟增设第二个键 `strategy_pretest`（「策略前测」职能名），落码时撤销**：
#    覆盖性闸门实测证明它是**零消费者的死键**（16 键里唯独它无端点绑定）——
#    正是本仓在录的「声明有·真源无」缺陷家族（批 76/77 同型病第三形态）。
#    按「不留死构件」立法，**职能标注不配拥有独立的 api 键**：键=端点准入，不是职责说明。
#    ⇒ analyst 的「前测职责」由 `paper_trade` 这一个键承载（有端点为证），
#      职责语义写在文档与界面文案里，不写进权限键表。
# ⚠️ **加键必补表行**（批 77 P0）：迁移 `0126` 补 `permission` 表 api 维行。字典（PERMISSIONS）
# 只是新环境兜底——表有行即完全盖住字典。
API_PERM_KEYS: list[str] = [
    "read", "strategy_control", "live_control", "data_sync", "halt", "resume", "trade",
    "live_trading_control", "risk_rules", "user_mgmt",
    "system_config", "llm_config", "im_bots_config", "alerts_config",
    "paper_trade",
]

# nav 条目 18（id+分组码+序——与原 NAV_ITEMS 声明序逐项一致，A-P1-3 等价钉；
# group 字段名保留原样：PermMatrix 列 prop="group" 直绑，改名即列空）
NAV_ITEMS_BASE: list[dict] = [
    {"id": "dashboard", "group": "base", "order": 1},
    {"id": "screener", "group": "research", "order": 1},
    {"id": "pool", "group": "research", "order": 2},
    {"id": "factors", "group": "research", "order": 3},
    {"id": "strategy", "group": "research", "order": 4},
    {"id": "backtest", "group": "research", "order": 5},
    {"id": "analysis", "group": "research", "order": 6},
    # 批 86-B：纸上交易（paper-trade）**独立菜单项**，非实盘任务页的 tab。
    # 理由（设计文档 §2.2.1）：tab 切换不经过路由 ⇒ nav 维管不到 tab 级 ⇒
    # analyst 会陷入「设 hidden 连测试 tab 也进不去 / 保留则菜单名与内容不符」的两难。
    # 独立页后：`live-task` 对 analyst hidden、`paper-trade` readwrite，
    # analyst 的「实盘」组只剩纸上交易一项（达成「一个角色功能集中一组」）。
    {"id": "live-task", "group": "live", "order": 1},
    {"id": "paper-trade", "group": "live", "order": 2},
    {"id": "trading", "group": "live", "order": 3},   # 批86-B：原 order 2，随 paper-trade 入册顺延
    {"id": "risk", "group": "riskgrp", "order": 1},
    {"id": "reconcile", "group": "riskgrp", "order": 2},
    {"id": "risk-rules", "group": "riskgrp", "order": 3},
    {"id": "users", "group": "ops", "order": 1},
    {"id": "dataops", "group": "ops", "order": 2},
    {"id": "routing", "group": "ops", "order": 3},       # 批 57 M2：数据路由（策略权重+dry-run 试算器+审计）
    {"id": "integrations", "group": "ops", "order": 4},
    {"id": "observe", "group": "ops", "order": 5},
    {"id": "perm-resources", "group": "ops", "order": 6},   # 批33b 入册；批37 组内序=系统权限前系统设置底
    {"id": "settings", "group": "ops", "order": 7},
]

# 市场操作键 5（原 perms._MARKET_OP_KEYS 字面量迁此——perms 改派生 re-export 保调用方零改动）
MARKET_OP_KEYS: tuple[str, ...] = ("convertible", "etf", "astock", "binance_perp", "okx_perp")

# 路由别名（原 MainLayout ROUTE_TO_NAV 硬编码 10 条实测——批33a 删 permissions 后；
# 批38：8 条死别名已清（见下））
NAV_ALIASES: dict[str, str] = {
    # 批38 死别名清理（快审 A-P2-1：仅参数路由别名活——redirect 类别名在 vue-router 解析后
    # 永不进守卫，删除零影响；活别名=stock（/stock/:symbol）+data-manage（/data-manage/:syncId））
    "data-manage": "dataops",
    "stock": "analysis",
}
# /chat 有路由无菜单不入 nav 维（A-P2-2 豁免注记——菜单本就不列，nav 权限管不到属预期）

_REGISTRY_CACHE: dict = {"at": 0.0, "data": None}
_REGISTRY_TTL = 60.0


def invalidate_registry_cache() -> None:
    """perm_resource 写后即刻生效（与 _PERM_CACHE 互不失效——A-P1-8 两缓存独立）。"""
    _REGISTRY_CACHE.update(at=0.0, data=None)


def load_registry() -> dict:
    """代码底座 ⊕ DB 覆盖层（perm_resource 四字段）。读失败/零行=纯底座；失败不缓存。

    返回 {api: [key...], nav: [{id,group,order,aliases?,label?,enabled?}...],
    market_op: [key...]}——nav 条目下发 shape：id/group 原名保留（A-P1-3），
    aliases/label/enabled 只增不改；api/market 键序恒底座序（覆盖层不改键集）。
    孤儿覆盖行（代码已删条目的残留）忽略+告警（A-P1-7）。"""
    now = time.time()
    if _REGISTRY_CACHE["data"] is not None and now - _REGISTRY_CACHE["at"] < _REGISTRY_TTL:
        return _REGISTRY_CACHE["data"]
    nav = [dict(e, aliases=[]) for e in NAV_ITEMS_BASE]
    for e in nav:
        e["aliases"] = [a for a, tgt in NAV_ALIASES.items() if tgt == e["id"]]
    registry = {"api": list(API_PERM_KEYS), "nav": nav, "market_op": list(MARKET_OP_KEYS)}
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT kind, res_id, group_key, sort_order, label_json, enabled "
                "FROM perm_resource").fetchall()
    except Exception as e:
        _logger.warning("perm_resource 读失败（回退代码底座，不缓存）: %s", e)
        return registry   # 失败不缓存——下次重试（perms.py 先例）
    nav_by_id = {e["id"]: e for e in registry["nav"]}
    for kind, res_id, group_key, sort_order, label_json, enabled in rows:
        if kind != "nav" or res_id not in nav_by_id:
            if kind == "nav":
                _logger.warning("perm_resource 孤儿覆盖行（代码已删条目）: %s/%s 忽略", kind, res_id)
            continue   # api/market 键集不可覆盖（红线）；未知 id 忽略
        e = nav_by_id[res_id]
        if group_key:
            e["group"] = group_key
        if sort_order is not None:
            e["order"] = sort_order
        if label_json:
            try:
                import json as _json
                e["label"] = _json.loads(label_json) if isinstance(label_json, str) else label_json
            except (ValueError, TypeError):
                pass
        if enabled is not None:
            e["enabled"] = bool(enabled)
    # A-P1-2 修：组序=底座声明序组秩（非字母序——原 NAV_ITEMS 声明序 base→research→live→riskgrp→ops
    # 等价钉）；未知覆盖组（管理页新造组名）排末尾组内按 order
    _rank = {g: i for i, g in enumerate(dict.fromkeys(e["group"] for e in NAV_ITEMS_BASE))}
    registry["nav"].sort(key=lambda e: (_rank.get(e["group"], 999), e["order"], e["id"]))
    _REGISTRY_CACHE.update(at=now, data=registry)
    return registry


# ——— 绑定扫描（阶段二可读+校验；A-P0-1 router-walk） ———

def scan_perm_bindings(routers: list | None = None) -> list[dict]:
    """扫描全部 APIRouter 的 require_perm 绑定 → [{path, methods, key}]。

    A-P0-1：FastAPI 0.141.1 app.routes 懒 include（_IncludedRouter 占位，实测 0 APIRoute）
    ——必须扫 APIRouter 实例。routers 参数供测试喂临时路由（造未注册键断言违例），
    缺省 sys.modules 收集（实测 14 只 router、175 处绑定[173+本批两端点]、14 键全量提取）。
    _perm_key 挂在 require_perm 返回的 checker 闭包上（web_api/auth.py 消费方约定）。"""
    if routers is None:
        import sys as _sys

        from fastapi import APIRouter as _AR
        try:
            mods = list(_sys.modules.values())   # 并发 import 期理论竞态——A-P2-5 兜
        except RuntimeError:
            mods = []
        routers = []
        seen = set()
        for mod in mods:
            try:
                attrs = vars(mod).values()
            except TypeError:
                continue
            for attr in attrs:
                if isinstance(attr, _AR) and id(attr) not in seen:
                    seen.add(id(attr))
                    routers.append(attr)
    out: list[dict] = []
    for r in routers:
        for route in getattr(r, "routes", []):
            key = _extract_perm_key(getattr(route, "dependant", None))
            if not key:
                continue
            # 批 86-B：多键绑定（`require_perm_any`）**展开成多条记录**，每条 `key` 仍是 str。
            # 为什么不让 key 保持 list：本函数的消费者（`GET /permissions` 的绑定视图、
            # 测试里的 `{x["key"] for x in b}` 集合化、drift 检查）**全都假定 key 是 str**
            # ——返回 list 会直接抛 `TypeError: unhashable type: 'list'`（实测踩到）。
            # 展开是**对外零契约变更**的解法：调用方看到的是「同一 path 有两条绑定」，语义正确。
            keys = key if isinstance(key, list) else [key]
            for k in keys:
                out.append({"path": getattr(route, "path", ""),
                            "methods": sorted(getattr(route, "methods", None) or []),
                            "key": k})
    return out


def _extract_perm_key(dependant) -> str | list[str] | None:
    """递归依赖树找 checker._perm_key（Dependant.call=闭包；Depends 对象在图中不保留）。

    批 86-B：`_perm_key` **可为 list**（`require_perm_any` 牵涉多键时）——
    原实现只认 str，list 会被当作 truthy 直接返回而下游 `not in known` 抛
    `TypeError: unhashable type: 'list'`；drift 检查侧已按 list 展开处理。
    """
    if dependant is None:
        return None
    call = getattr(dependant, "call", None)
    key = getattr(call, "_perm_key", None)
    if key:
        return key
    for sub in getattr(dependant, "dependencies", None) or []:
        found = _extract_perm_key(sub)
        if found:
            return found
    return None


def check_binding_drift(routers: list | None = None) -> list[str]:
    """防漂移闸：绑定键集 ⊆ 注册表 api 键集——违例清单（启动告警+测试断言共用）。

    批 86-B：多键绑定已在 `scan_perm_bindings` 内展开成多条 str 记录，
    故此处无需特判（key 恒为 str）。
    """
    reg = load_registry()
    known = set(reg["api"])
    return [f"{b['path']} → {b['key']}" for b in scan_perm_bindings(routers)
            if b["key"] not in known]
