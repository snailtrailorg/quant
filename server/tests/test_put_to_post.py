"""PUT→POST 硬切（A 案）冒烟测试。

I 审两个焦点：
1. 16 个原 PUT 端点 POST 可达（401/422 皆可——只要不是 405/404）
2. **路由遮蔽**：POST 化后参数化路由（{sid}/{bid}/{name}）会吃掉后注册的静态路由——
   已把 4 个静态端点调到前面注册，此测试锁死注册顺序（回退/重排即红）。
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from src.web_api.main import app
    return TestClient(app)


# 16 个原 PUT 端点
POST_ENDPOINTS = [
    "/api/smtp-config",
    "/api/system-config/some-key",
    "/api/user/profile",
    "/api/user/1",
    "/api/strategy/some-id",
    "/api/account/1",
    "/api/live-trading/astock",
    "/api/llm-models/1",
    "/api/im-bots/1",
    "/api/sync/config/astock_daily",
    "/api/llm-budget/1",
    "/api/data-sources/1",
    "/api/channels/1",
    "/api/brokers/1",
    "/api/risk-rules/1",
    "/api/factors/some-name",
]

# 4 个曾被遮蔽高危的静态端点（必须注册在参数化兄弟之前）
STATIC_ENDPOINTS = [
    "/api/strategy/validate-python",
    "/api/strategy/validate-params",
    "/api/llm-budget/check",
    "/api/factors/validate",
]


class TestPutToPost:
    def test_all_post_endpoints_reachable(self, client):
        """POST 到全部 16 端点：鉴权/校验拦截（401/403/422）都算可达——405/404 是动词/路由错。"""
        for url in POST_ENDPOINTS:
            r = client.post(url, json={})
            assert r.status_code not in (405, 404), f"{url} POST 不可达: {r.status_code}"

    def test_put_now_returns_405(self, client):
        """老动词负向验证：PUT 应 405（证明硬切干净，无双路由残留）。"""
        r = client.put("/api/smtp-config", json={})
        assert r.status_code == 405

    def test_static_routes_not_shadowed(self, client):
        """J-S1 重写：结构化顺序断言——同方法下，任何静态路径不得被**更早注册**的参数化
        路由正则匹配（遮蔽）。覆盖全部路由而非 4 个白名单端点，回退/重排即红，零 DB。
        （原 401-即-可达版是死测试：遮蔽时 auth 依赖先失败同样 401，变异实测不红。）
        """
        from starlette.routing import Route
        routes = client.app.routes
        for idx, static in enumerate(routes):
            if not isinstance(static, Route) or "{" in static.path:
                continue
            for method in (static.methods or ()):
                for j in range(idx):
                    param = routes[j]
                    if (isinstance(param, Route) and method in (param.methods or ())
                            and "{" in param.path and param.path_regex.match(static.path)):
                        pytest.fail(
                            f"{method} {static.path} 被先注册的参数化路由 {param.path} 遮蔽"
                            f"（Starlette 按注册序匹配，静态路由必须注册在前）")

    def test_shadow_detection_via_body_marker(self, client):
        """遮蔽侦测（body 区分）：validate-python 带合法空 code 应返回 valid 判定结构；
        若落入 {{sid}} 更新路由则 422（StrategyConfig 必填校验先于 handler）。"""
        from unittest.mock import patch
        with patch("src.web_api.auth.verify_jwt",
                   return_value={"sub": "1", "username": "admin", "role": "admin"}):
            r = client.post("/api/strategy/validate-python", json={"code": "x = 1"},
                            headers={"Authorization": "Bearer t"})
        assert r.status_code == 200 and "valid" in r.json()
