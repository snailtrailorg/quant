"""守门测试：全站路由认证冻结（批 79 部署后待办 2026-09-30 止血）。

背景：routes/quality.py 两端点曾把 require_perm(...) 直接写成参数默认值（缺
Depends 包裹），FastAPI 不视为依赖——认证检查整体被跳过，裸 curl 返回 200。
绑定扫描（test_batch33b router-walk）只断「键 ⊆ 注册表」，扫不到这种漏网——
依赖树里根本没有 checker，绑定计数不含它，故需本测试补「无认证端点白名单冻结」维。

机制：递归走每条路由的 dependant 依赖树，断言至少一个依赖是
require_authenticated 或 require_perm checker（_perm_key 属性）。
白名单=当前实测 17 条合法无认证端点，禁增量。
"""
import sys

from fastapi import APIRouter
from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

# 合法无认证端点白名单（path, methods 冻结对；ws 路由 methods=[]）。
# 语义分组：
#   健康探针/指标/条款: /health /healthz /readyz /metrics /api/terms /api/_probe
#   认证入口本体:       /api/auth/login /api/auth/register /api/auth/forgot-password
#                       /api/auth/reset-password /api/auth/invite/verify
#                       /api/user/email-change/confirm（邮件 token 自认证）
#   回调（签名校验）:   /lark/webhook /lark/card/callback /lark/test
#   WebSocket（连接时鉴权）: /ws/chat /ws/market
NO_AUTH_ALLOWLIST = {
    ("/lark/webhook", "POST"),
    ("/lark/card/callback", "POST"),
    ("/lark/test", "GET"),
    ("/api/auth/login", "POST"),
    ("/api/user/email-change/confirm", "POST"),
    ("/api/auth/invite/verify", "GET"),
    ("/api/auth/register", "POST"),
    ("/api/auth/forgot-password", "POST"),
    ("/api/auth/reset-password", "POST"),
    ("/ws/chat", ""),
    ("/ws/market", ""),
    ("/api/_probe", "GET"),
    ("/health", "GET"),
    ("/healthz", "GET"),
    ("/readyz", "GET"),
    ("/metrics", "GET"),
    ("/api/terms", "GET"),
}


def _all_deps(dependant):
    out = []
    for dd in dependant.dependencies:
        out.append(dd)
        out.extend(_all_deps(dd))
    return out


def _is_auth_dep(dd) -> bool:
    """require_authenticated 本体或 require_perm checker（_perm_key 闭包属性）。"""
    return (getattr(dd.call, "__name__", "") == "require_authenticated"
            or hasattr(dd.call, "_perm_key"))


def _collect_routers():
    import src.web_api.main  # noqa: F401  注册全部路由模块（懒 include，须扫 APIRouter 实例）
    routers, seen = [], set()
    for mod in list(sys.modules.values()):
        try:
            attrs = vars(mod).values()
        except TypeError:
            continue
        for attr in attrs:
            if isinstance(attr, APIRouter) and id(attr) not in seen:
                seen.add(id(attr))
                routers.append(attr)
    return routers


def test_every_route_has_auth_or_is_allowlisted():
    """每条路由（HTTP+WS）必须挂认证依赖，或精确命中白名单（禁增量）。"""
    missing = []
    for r in _collect_routers():
        for route in r.routes:
            if not isinstance(route, (APIRoute, WebSocketRoute)):
                continue
            deps = _all_deps(route.dependant)
            if any(_is_auth_dep(dd) for dd in deps):
                continue
            methods = "".join(sorted(getattr(route, "methods", None) or []))
            entry = (route.path, methods)
            if entry not in NO_AUTH_ALLOWLIST:
                missing.append(entry)
    assert not missing, (
        f"发现无认证端点（须 require_perm/require_authenticated 依赖，或先更新本测试白名单并说明理由）：{missing}")


def test_allowlist_still_accurate():
    """白名单反向校验：每条白名单项仍真实无认证（防白名单腐化成摆设）。"""
    noauth_paths = set()
    for r in _collect_routers():
        for route in r.routes:
            if not isinstance(route, (APIRoute, WebSocketRoute)):
                continue
            deps = _all_deps(route.dependant)
            if not any(_is_auth_dep(dd) for dd in deps):
                methods = "".join(sorted(getattr(route, "methods", None) or []))
                noauth_paths.add((route.path, methods))
    stale = NO_AUTH_ALLOWLIST - noauth_paths
    assert not stale, (
        f"白名单条目已不再是『无认证端点』（端点加了认证，请从白名单删除）：{sorted(stale)}")


def test_sm_reconcile_rejects_unauthenticated():
    """行为级回归钉（批 79 待办根因）：裸 curl 不再 200——缺 token 422/401，假 token 401/403。"""
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    client = TestClient(app)
    r1 = client.get("/api/quality/sm-reconcile")
    assert r1.status_code != 200, "未认证请求竟返回 200——require_perm 依赖又没挂上"
    assert r1.status_code in (401, 403, 422)
    with patch("src.web_api.auth.verify_jwt",
               return_value={"sub": 1, "username": "admin", "role": "admin", "db_role": "admin"}), \
         patch("src.web_api.auth.load_effective_permissions",
               return_value=({"system_config", "read"}, set())):
        client.headers.update({"Authorization": "Bearer faketoken"})
        r2 = client.get("/api/quality/sm-reconcile")
    assert r2.status_code == 200, f"带合法 token 应 200，实得 {r2.status_code}"
