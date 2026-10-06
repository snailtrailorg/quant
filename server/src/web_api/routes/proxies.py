"""批 102a：代理配置面（全局池 + 按消费方绑定 + 测连通）。

    - `GET    /api/proxies`                      池列表（url 掩码）+ 可配消费方
    - `POST   /api/proxies`                      新增/更新一行（system_config）
    - `DELETE /api/proxies/{name}`               删一行（system_config）
    - `GET    /api/proxies/capable-consumers`    可配代理的消费方（read）
    - `GET    /api/proxies/bindings`             绑定列表（read）
    - `POST   /api/proxies/bindings/{consumer}`  写一条绑定（system_config）
    - `DELETE /api/proxies/bindings/{consumer}`  删一条绑定（system_config）
    - `POST   /api/proxies/{name}/probe`         经该代理探出网（system_config）

（注：上表以项目符号起首是**刻意**的——`tests/test_no_sql_in_new_routes.py` 的 SQL 冻结
按 ast 字符串字面量扫全文，docstring 行若以 SQL 关键字起首会被计入「内联 SQL」。）

**分层**：SQL 全在 `src/data_platform/proxy.py`（层 1）；本文件**零 DB 连接、零 SQL 字面量**
——`tests/test_no_sql_in_new_routes.py` 的白名单外新文件档对新建路由文件自动生效（无需登记基线）。

**权限**：复用配置面既有键（`read` 读 / `system_config` 写），**不新增 perm 键**——避免
「新 perm 键四条齐动」（API_PERM_KEYS / PERMISSIONS / seed 迁移 / 前端 permGroups）的连锁。

**动词纪律**：本仓已做 **PUT→POST 硬切**（`tests/test_put_to_post.py` 锁死）⇒ 写端点一律 POST，
**不新增 PUT**。

**注册顺序（遮蔽防御，同 `test_put_to_post` 的静态端点纪律）**：`bindings` 是**静态段**，
必须在参数化兄弟 `{name}` 之前注册——`/api/proxies/bindings/{consumer}` 与
`/api/proxies/{name}/probe` 都是四段路径，`POST /api/proxies/bindings/probe` 会同时命中两者，
先注册者胜。故本文件把 bindings 段写在最前；`tests/test_batch102a_proxy.py` 有遮蔽钉。

**掩码**：池列表的 url **只替 userinfo**（scheme/host/port 原样），与凭证掩码同模式；
`probe` 是唯一用未掩码 url 的路径，且它**不把 url 回给客户端**。
"""
from fastapi import APIRouter, Body, Depends

from src.data_platform import proxy as proxy_store

from ..auth import audit_log, require_perm
from ..errors import ApiError

router = APIRouter(tags=["proxies"])


def _bad(e: Exception) -> ApiError:
    return ApiError(400, "PARAM_INVALID", str(e))


def _assert_consumer_capable(consumer: str) -> None:
    """配了要**生效**才算配置（否则是静默无效配置——本仓禁）。判据真源＝adapter 类属性
    （`supports_exit_config`），随 adapter 接线自动长大（102b 的 okx 届时自动进集合）。"""
    capable = proxy_store.capable_consumers()
    if consumer not in capable:
        raise ApiError(
            400, "CONSUMER_NOT_PROXYABLE",
            f"消费方 {consumer!r} 的 adapter 未接线代理出口；当前可配：{capable}")


# ═══════════ bindings（静态段，必须先于 {name} 参数化兄弟注册）═══════════


@router.get("/api/proxies/bindings")
def list_bindings(payload: dict = Depends(require_perm("read"))):
    return {"items": proxy_store.list_bindings()}


@router.post("/api/proxies/bindings/{consumer}")
def set_binding(consumer: str, body: dict = Body(...),
                payload: dict = Depends(require_perm("system_config"))):
    _assert_consumer_capable(consumer)
    try:
        proxy_store.set_binding(
            consumer,
            bool(body.get("proxy_enabled", False)),
            body.get("proxy_name"),
            body.get("endpoint_override"),
        )
    except ValueError as e:
        raise _bad(e)
    audit_log(payload["username"], "proxy_binding_set",
              f"{consumer} enabled={bool(body.get('proxy_enabled', False))} "
              f"name={body.get('proxy_name')}")
    return {"ok": True, "consumer": consumer}


@router.delete("/api/proxies/bindings/{consumer}")
def delete_binding(consumer: str, payload: dict = Depends(require_perm("system_config"))):
    if not proxy_store.delete_binding(consumer):
        raise ApiError(404, "NOT_FOUND", f"绑定 {consumer!r} 不存在")
    audit_log(payload["username"], "proxy_binding_delete", f"{consumer}")
    return {"ok": True}


@router.get("/api/proxies/capable-consumers")
def capable_consumers(payload: dict = Depends(require_perm("read"))):
    return {"items": proxy_store.capable_consumers()}


# ═══════════════════════════ 代理池 ═══════════════════════════


@router.get("/api/proxies")
def list_proxies(payload: dict = Depends(require_perm("read"))):
    return {"items": proxy_store.list_proxies(mask=True),
            "capable_consumers": proxy_store.capable_consumers()}


@router.post("/api/proxies")
def upsert_proxy(body: dict = Body(...), payload: dict = Depends(require_perm("system_config"))):
    name = str(body.get("name") or "").strip()
    if not name:
        raise ApiError(400, "PARAM_INVALID", "name 必填")
    try:
        proxy_store.upsert_proxy(name, body.get("url"), body.get("notes"),
                                 bool(body.get("enabled", True)))
    except ValueError as e:
        raise _bad(e)
    audit_log(payload["username"], "proxy_upsert", f"{name}")
    return {"ok": True, "name": name}


@router.delete("/api/proxies/{name}")
def delete_proxy(name: str, payload: dict = Depends(require_perm("system_config"))):
    try:
        ok = proxy_store.delete_proxy(name)
    except Exception as e:
        # FK 是 ON DELETE SET NULL，但「已启用却无所指」会被 CHECK 拒——属配置态问题，不是 500
        raise ApiError(409, "PROXY_IN_USE",
                       f"代理 {name!r} 仍被启用的绑定引用（先把该消费方的开关关掉）：{e}")
    if not ok:
        raise ApiError(404, "NOT_FOUND", f"代理 {name!r} 不存在")
    audit_log(payload["username"], "proxy_delete", f"{name}")
    return {"ok": True}


@router.post("/api/proxies/{name}/probe")
def probe_proxy(name: str, body: dict | None = Body(default=None),
                payload: dict = Depends(require_perm("system_config"))):
    """经该代理 GET 目标（默认 ipify）→ 出口 IP + 延迟。

    **失败不抛 500**（诊断端点的返回值本身就是结果）：返回 `{"ok": false, "error": …}`，
    由前端亮错误条。`target` 可覆盖成真实 provider 端点，验证「该源是否真能出网」。
    """
    target = (body or {}).get("target")
    if target is not None and not isinstance(target, str):
        raise ApiError(400, "PARAM_INVALID", "target 须为字符串")
    return proxy_store.probe(name, target=target)
