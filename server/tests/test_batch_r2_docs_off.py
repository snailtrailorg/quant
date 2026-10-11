"""安全评估 #1（2026-10-11）：生产关闭 /docs /redoc /openapi.json。

背景：三者公网匿名 200——API 全貌（端点/参数/schema）对侦察者可见。
修：FastAPI(docs_url/redoc_url/openapi_url=None)；SHOW_DOCS=1 可开回（dev 调试）。
"""
import importlib


def _get_routes(m):
    """路由对象形态不一（APIRoute 有 path；_IncludedRouter 无）——取 hasattr 过滤。"""
    return {r.path for r in m.app.routes if hasattr(r, "path")}


def test_docs_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SHOW_DOCS", raising=False)
    import src.web_api.main as m
    importlib.reload(m)
    paths = _get_routes(m)
    assert "/docs" not in paths
    assert "/redoc" not in paths
    assert "/openapi.json" not in paths


def test_docs_enabled_with_env(monkeypatch):
    monkeypatch.setenv("SHOW_DOCS", "1")
    import src.web_api.main as m
    importlib.reload(m)
    paths = _get_routes(m)
    assert "/docs" in paths
    assert "/openapi.json" in paths
    # 还原模块状态（避免污染其他测试的 app 单例）
    monkeypatch.delenv("SHOW_DOCS", raising=False)
    importlib.reload(m)


def test_api_routes_untouched(monkeypatch):
    """关文档不伤业务路由：reload 后 include_router 是惰性壳（_IncludedRouter 展开
    在首请求），改验证 TestClient 实际可达两个锚点端点（401/422=路由在，404=没了）。"""
    monkeypatch.delenv("SHOW_DOCS", raising=False)
    import src.web_api.main as m
    importlib.reload(m)
    from fastapi.testclient import TestClient
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.post("/api/auth/login", json={}).status_code in (400, 422)
    assert c.post("/api/sync/trigger/x", headers={"Authorization": "Bearer t"}).status_code in (400, 401, 422)
