"""批33b · 权限资源注册表化单测（方案 v3=双盲审 A P0×2+P1×8 / B P0×1+P1×8 全吸收）。

覆盖：注册表底座三清单/载入容错（失败回底座不缓存）/绑定扫描 router-walk（173 量级
实证）/防漂移闸（未注册键违例）/GET /permissions 供形等价（id/group 原名+aliases 增量）/
me 带 nav_aliases（P0-2 真源）/PATCH 三拒+合法 upsert+缓存失效/admin 集注册表派生等价。
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import APIRouter, Depends


@pytest.fixture(scope="module")
def client():
    from src.web_api.main import app
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    from src.web_api import auth as _auth
    with patch.object(_auth, "verify_jwt",
                      return_value={"sub": 1, "username": "u1", "role": "admin", "db_role": "admin"}):
        client.headers.update({"Authorization": "Bearer test-token"})
        yield client
        client.headers.pop("Authorization", None)


def test_registry_base_three_lists():
    """底座三清单：api 14/nav 18（含管理页自身吃狗粮）/market 5——与原三份平行清单等价收编。"""
    from src.data_platform.perm_registry import API_PERM_KEYS, NAV_ITEMS_BASE, MARKET_OP_KEYS, NAV_ALIASES
    assert len(API_PERM_KEYS) == 14 and len(set(API_PERM_KEYS)) == 14
    assert len(NAV_ITEMS_BASE) == 18   # 17+perm-resources（批33b 管理页）
    assert len(MARKET_OP_KEYS) == 5
    assert len(NAV_ALIASES) == 2       # 批38：8 条死别名已清（活=stock/data-manage）


def test_load_registry_fallback_on_db_fail():
    """载入容错（B-P1-3）：DB 读失败回退底座+不缓存（at 不推进）。"""
    from src.data_platform import perm_registry as PR
    PR._REGISTRY_CACHE.update(at=0.0, data=None)
    with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db down")):
        reg1 = PR.load_registry()
        assert len(reg1["nav"]) == 18
        assert PR._REGISTRY_CACHE["data"] is None   # 失败不缓存


def test_scan_bindings_full_and_drift():
    """router-walk 扫描（A-P0-1：app.routes 懒 include 拿 0 条——本实现扫 APIRouter 实例）
    ：import 真 app 后 173 处量级+14 键+违例 0；临时 router 喂未注册键=违例非空。"""
    from src.data_platform.perm_registry import scan_perm_bindings, check_binding_drift
    import src.web_api.main
    assert src.web_api.main  # 注册全部路由（真用防 pyflakes）
    b = scan_perm_bindings()
    assert len(b) >= 170 and len({x["key"] for x in b}) == 14
    assert check_binding_drift() == []
    r = APIRouter()
    @r.get("/api/__unreg_test")
    def _t(payload: dict = Depends(__import__("src.web_api.auth", fromlist=["require_perm"]).require_perm("__nope__"))):
        return {}
    drift = check_binding_drift(routers=[r])
    assert drift and "__nope__" in drift[0]


def test_get_permissions_shape_compat(authed_client):
    """供形等价（A-P1-3）：keys=注册表序 14；nav.items id/group 原名保留+aliases/enabled 增量。"""
    with patch("src.web_api.routes.auth_routes.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.auth.load_role_permissions", return_value={}), \
         patch("src.web_api.routes.auth_routes._load_dim", return_value={}), \
         patch("src.web_api.routes.auth_routes._all_group_names", return_value=["admin"]):
        r = authed_client.get("/api/permissions")
    assert r.status_code == 200
    j = r.json()
    assert len(j["keys"]) == 14 and j["keys"][0] == "read"
    nav = j["nav"]["items"]
    assert all("id" in e and "group" in e for e in nav)      # 原名保留（PermMatrix prop=group 直绑）
    # A-P1-2 修后等价钉：前 17 项 id 序==原 NAV_ITEMS 声明序（组秩排序非字母序）
    from src.data_platform.perm_registry import NAV_ITEMS_BASE
    assert [e["id"] for e in nav[:17]] == [e["id"] for e in NAV_ITEMS_BASE[:17]]
    assert any(e.get("aliases") for e in nav)                 # 增量字段（别名为活别名的条目）
    assert any(e["id"] == "perm-resources" for e in nav)      # 管理页自身入册


def test_me_carries_nav_aliases(authed_client):
    """P0-2：别名真源=me 面（守卫服务全员——/permissions 面 admin-only 拿不到）。"""
    with patch("src.web_api.routes.auth_routes.require_authenticated",
               return_value={"sub": 1, "username": "u1", "db_role": "viewer"}):
        r = authed_client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json().get("nav_aliases", {}).get("stock") == "analysis"


def _admin_dep():
    from src.web_api import auth as _auth
    with patch.object(_auth, "verify_jwt",
                      return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        pass
    return {"sub": 1, "username": "admin", "db_role": "admin"}


def test_patch_endpoint_guards(authed_client):
    """PATCH 三拒（kind/api 禁改、未知 id、坏值）+合法 upsert+缓存失效。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    with patch("src.web_api.routes.auth_routes.get_conn", return_value=conn), \
         patch("src.web_api.routes.auth_routes.require_perm", return_value=_admin_dep()), \
         patch("src.web_api.routes.auth_routes.audit_log"):
        r1 = authed_client.patch("/api/perm-resources/api/read", json={"enabled": False})
        assert r1.status_code == 400 and r1.json().get("code") == "PERM_RES_KIND"
        r2 = authed_client.patch("/api/perm-resources/nav/ghost-page", json={"group_key": "ops"})
        assert r2.status_code == 400 and r2.json().get("code") == "PERM_RES_UNKNOWN"
        r3 = authed_client.patch("/api/perm-resources/nav/settings", json={"sort_order": -1})
        assert r3.status_code == 400 and r3.json().get("code") == "PERM_RES_VALUE"
        from src.data_platform import perm_registry as PR
        PR._REGISTRY_CACHE.update(at=1e12, data={"stale": True})   # 预置陈旧缓存
        r4 = authed_client.patch("/api/perm-resources/nav/settings",
                                 json={"group_key": "ops", "sort_order": 9, "label_json": None, "enabled": True})
        assert r4.status_code == 200
        assert PR._REGISTRY_CACHE["data"] is None                  # invalidate 生效


def test_perm_resources_get_shape(authed_client):
    """管理页 GET：三段+绑定反查（api 条目带 endpoints 清单+locked 标注）+总数。"""
    with patch("src.web_api.routes.auth_routes.require_perm", return_value=_admin_dep()), \
         patch("src.web_api.routes.auth_routes.audit_log"):
        r = authed_client.get("/api/perm-resources")
    assert r.status_code == 200
    j = r.json()
    assert len(j["api"]) == 14 and j["bindings_total"] >= 170
    user_mgmt = next(a for a in j["api"] if a["key"] == "user_mgmt")
    assert user_mgmt["locked"] is True and user_mgmt["endpoints"]


def test_admin_set_derived_equivalence():
    """A-P1-4：PERMISSIONS['admin']=注册表 api 键集派生（三集等价收编断言）。"""
    from src.data_platform.perms import PERMISSIONS
    from src.data_platform.perm_registry import API_PERM_KEYS
    assert PERMISSIONS["admin"] == set(API_PERM_KEYS)


@pytest.fixture(autouse=True)
def _clean_registry_cache():
    """批33b 测试间清注册表缓存（patch 测试预置 stale dict 会污染后续 GET——B 审跑序问题）。"""
    from src.data_platform import perm_registry as PR
    yield
    PR._REGISTRY_CACHE.update(at=0.0, data=None)


def test_overlay_end_to_end_real_db():
    """B-P1-4：覆盖层正向生效真库端到端——PATCH 写行→GET /permissions 形状变化（合并+排序）→清零还原。
    （批34「本地真库热路径冒烟」先例——SQL 真到 psycopg，非 mock。）"""
    from src.data_platform.db import get_conn
    from src.data_platform.perm_registry import invalidate_registry_cache, load_registry
    with get_conn() as conn:
        conn.execute("DELETE FROM perm_resource WHERE res_id='settings'")
        conn.commit()
    try:
        invalidate_registry_cache()
        reg1 = load_registry()
        assert next(e for e in reg1["nav"] if e["id"] == "settings")["group"] == "ops"
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO perm_resource (kind, res_id, group_key, sort_order, enabled) "
                "VALUES ('nav', 'settings', 'research', 42, false)")
            conn.commit()
        invalidate_registry_cache()
        reg2 = load_registry()
        se = next(e for e in reg2["nav"] if e["id"] == "settings")
        assert se["group"] == "research" and se["order"] == 42 and se.get("enabled") is False   # 覆盖生效
        assert se in [e for e in reg2["nav"] if e["group"] == "research"]                        # 组秩排序重聚
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM perm_resource WHERE res_id='settings'")
            conn.commit()
        invalidate_registry_cache()
