"""批11B 用户组动态化测试（方案双盲审 A/B 修订后）。

覆盖：①组 CRUD+builtin 锁②rename 三表级联+尾断言+invalidate③删组拒/级联/软删归位
④update_permissions 动态白名单+锁键地板（自定义组恒拿不到）⑤require_authenticated
（自定义组用户身份端点通——双盲审 P0-1）⑥create/update user 角色动态校验
⑦GET permissions 表空回退⑧删组重建同名不继承（invalidate 防 60s 复活窗，P1）。
"""
from unittest.mock import patch, MagicMock

import pytest


def _conn(scripted=None):
    """可脚本化 conn：scripted=[(fetchone, fetchall, rowcount), ...] 按调用序消费 execute。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    calls = {"n": 0}
    def execute(sql, *a):
        i = calls["n"]; calls["n"] += 1
        cur = MagicMock()
        if scripted and i < len(scripted):
            cur.fetchone.return_value, cur.fetchall.return_value, cur.rowcount = scripted[i]
        else:
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []
            cur.rowcount = 0
        return cur
    conn.execute.side_effect = execute
    conn._calls = lambda: calls["n"]
    return conn


ADMIN = {"sub": "1", "username": "admin", "role": "admin", "db_role": "admin"}


def _client():
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    return TestClient(app)


def _admin_ctx(conn):
    """三 patch + 权限层短路：auth_routes 顶绑定 + 定义处 + verify_jwt 身份 +
    load_effective_permissions 恒 admin（require_perm 不碰 DB——scripted 槽位完全归端点函数体）。"""
    return [
        patch("src.web_api.auth.verify_jwt", return_value=ADMIN),
        patch("src.web_api.routes.auth_routes.get_conn", return_value=conn),
        patch("src.data_platform.db.get_conn", return_value=conn),
        patch("src.web_api.auth.load_effective_permissions",
              return_value=({"user_mgmt", "read"}, {})),
    ]


class TestGroupCrud:
    def test_create_bad_name(self):
        import contextlib
        conn = _conn()
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().post("/api/user-groups", json={"name": "Bad Name!", "description": ""}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 400 and r.json()["code"] == "GROUP_NAME_INVALID"

    def test_create_ok_and_duplicate(self):
        import contextlib
        conn = _conn(scripted=[((42,), [], 1)])   # INSERT RETURNING id=42
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().post("/api/user-groups", json={"name": "quant_ops", "description": "运维组"}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 200 and r.json() == {"id": 42, "name": "quant_ops"}

        dup = _conn()
        dup.execute = MagicMock(side_effect=Exception("duplicate key value violates unique constraint"))
        dup.__enter__.return_value = dup
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(dup): s.enter_context(p)
            r = _client().post("/api/user-groups", json={"name": "quant_ops"}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 409 and r.json()["code"] == "GROUP_EXISTS"

    def test_list_excludes_soft_deleted_users(self):
        """user_count 只数活跃（WHERE deleted_at IS NULL）——SQL 钉子（盲审 B P1-2）。"""
        import contextlib
        conn = _conn(scripted=[(None, [(1, "admin", "d", True, 2), (5, "ops", "d2", False, 0)], 0)])
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().get("/api/user-groups", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        assert r.json()[0] == {"id": 1, "name": "admin", "description": "d", "builtin": True, "user_count": 2}
        sql = conn.execute.call_args_list[0][0][0]
        assert "deleted_at IS NULL" in sql


class TestGroupGuard:
    def test_delete_builtin_rejected(self):
        import contextlib
        conn = _conn(scripted=[(("admin", True), [], 0)])
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().delete("/api/user-groups/1", headers={"Authorization": "Bearer t"})
        assert r.status_code == 400 and r.json()["code"] == "GROUP_BUILTIN"

    def test_delete_in_use_rejected(self):
        import contextlib
        conn = _conn(scripted=[(("ops", False), [], 0), ((3,), [], 0)])   # 组行 → count=3
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().delete("/api/user-groups/5", headers={"Authorization": "Bearer t"})
        assert r.status_code == 409 and r.json()["code"] == "GROUP_IN_USE"

    def test_delete_cascades_and_invalidates(self):
        import contextlib
        conn = _conn(scripted=[(("ops", False), [], 0), ((0,), [], 0),         # 组行 → count=0
                               (None, [], 2),                                    # DELETE permission rowcount=2
                               (None, [], 1),                                    # UPDATE 软删归位 rowcount=1
                               (None, [], 1)])                                   # DELETE user_group
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            inv = s.enter_context(patch("src.web_api.auth.invalidate_perm_cache"))
            r = _client().delete("/api/user-groups/5", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        inv.assert_called_once()   # 盲审 A/B 同判 P1：防 60s 内重建同名组权限复活
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("DELETE FROM permission" in s and "subject_id" in s for s in sqls)
        assert any("role='viewer'" in s and "deleted_at IS NOT NULL" in s for s in sqls)


class TestGroupRename:
    def test_builtin_rename_rejected(self):
        import contextlib
        conn = _conn(scripted=[(("admin", True), [], 0)])
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().post("/api/user-groups/1", json={"name": "root", "description": "x"}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 400 and r.json()["code"] == "GROUP_BUILTIN"

    def test_rename_race_rollback(self):
        """尾断言：rename 事务尾仍有旧名 permission 行 → 回滚 409（盲审 B P2-1）。"""
        import contextlib
        conn = _conn(scripted=[(("ops", False), [], 0),      # 组行
                               (None, [], 0),                 # 重名预检（无撞名）
                               (None, [], 1), (None, [], 2), (None, [], 1),   # 三表 UPDATE
                               (None, [], 1),                                  # 软删归位（A-P2-3）
                               ((1,), [], 0)])               # 尾断言发现残留行
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().post("/api/user-groups/5", json={"name": "ops2", "description": ""}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 409 and r.json()["code"] == "GROUP_RENAME_RACE"
        conn.rollback.assert_called_once()

    def test_rename_ok_invalidates(self):
        import contextlib
        conn = _conn(scripted=[(("ops", False), [], 0),
                               (None, [], 0),                 # 重名预检（无撞名）
                               (None, [], 1), (None, [], 2), (None, [], 1), (None, [], 1),
                               (None, [], 0)])               # 尾断言干净
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            inv = s.enter_context(patch("src.web_api.auth.invalidate_perm_cache"))
            r = _client().post("/api/user-groups/5", json={"name": "ops2", "description": ""}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        inv.assert_called_once()
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert sum("UPDATE permission SET subject_id" in s for s in sqls) == 1
        assert sum("UPDATE users SET role" in s for s in sqls) == 2   # 活跃+软删归位（A-P2-3）


class TestPermSaveDynamic:
    """双盲审 P0-2：白名单动态化+地板逻辑保留。"""

    def test_ghost_role_rejected(self):
        import contextlib
        conn = _conn(scripted=[(None, [], 0)])   # 组校验 fetchone=None
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().post("/api/permissions/ghost", json={"permissions": ["read"]}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 400 and r.json()["detail"] == "用户组不存在: ghost"

    def test_custom_group_save_locks_stripped(self):
        """自定义组保存含锁键 → 落库恒不含（地板逐字保留的钉子测试）。"""
        import contextlib
        # 依次：组校验 fetchone=(1,)；load_role_permissions 走真函数会碰 DB——patch 它返回空
        conn = _conn(scripted=[((1,), [], 0)])
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            s.enter_context(patch("src.web_api.auth.load_role_permissions", return_value={}))
            r = _client().post("/api/permissions/ops", json={"permissions": ["read", "user_mgmt", "resume", "account_keys", "trade"]}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        inserts = [c[0][1] for c in conn.execute.call_args_list if "INSERT INTO permission" in c[0][0]]
        assert inserts, "应有 INSERT"
        for args in inserts:
            assert args[1] not in ("user_mgmt", "resume", "account_keys")   # 锁键恒不入库


class TestRequireAuthenticated:
    """双盲审 P0-1：自定义组（零权限）用户身份端点通。"""

    def test_custom_group_me_ok(self):
        import contextlib
        CUSTOM = {"sub": "9", "username": "u9", "role": "quant_ops", "db_role": "quant_ops"}
        conn = _conn(scripted=[(("nick", "/a.png", "quant_ops"), [], 0)])   # me 的 users 查询
        with contextlib.ExitStack() as s:
            s.enter_context(patch("src.web_api.auth.verify_jwt", return_value=CUSTOM))
            s.enter_context(patch("src.web_api.routes.auth_routes.get_conn", return_value=conn))
            s.enter_context(patch("src.web_api.auth.load_effective_permissions",
                                  return_value=(set(), {})))
            s.enter_context(patch("src.web_api.auth.load_nav_map", return_value={}))
            r = _client().get("/api/auth/me", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200 and r.json()["role"] == "quant_ops"

    def test_custom_group_logout_and_change_password_ok(self):
        """代码盲审补（A-P2-6/B-P2-3）：方案 §5 明列 logout+change-password——身份端点面钉住防漏换。"""
        import contextlib
        CUSTOM = {"sub": "9", "username": "u9", "role": "quant_ops", "db_role": "quant_ops"}
        with contextlib.ExitStack() as s:
            s.enter_context(patch("src.web_api.auth.verify_jwt", return_value=CUSTOM))
            s.enter_context(patch("src.web_api.auth.revoke_jwt", return_value=True))
            r = _client().post("/api/auth/logout", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        conn = _conn(scripted=[(("x" * 60,), [], 0), (None, [], 1)])   # 旧密码行 → UPDATE
        with contextlib.ExitStack() as s:
            s.enter_context(patch("src.web_api.auth.verify_jwt", return_value=CUSTOM))
            s.enter_context(patch("src.web_api.routes.auth_routes.get_conn", return_value=conn))
            s.enter_context(patch("src.web_api.routes.auth_routes.change_password", return_value=True))
            r = _client().post("/api/auth/change-password",
                               json={"old_password": "Xold1234", "new_password": "Abcd1234"},
                               headers={"Authorization": "Bearer t"})
        assert r.status_code == 200

    def test_delete_then_recreate_no_inherit(self):
        """删组重建同名不继承权限——invalidate 后缓存清空，新组零权限（代码盲审 A-P2-6 语义化断言）。"""
        import contextlib
        import src.web_api.auth as A
        import src.data_platform.perms as perms_mod
        conn = _conn(scripted=[(("ops", False), [], 0), ((0,), [], 0),
                               (None, [], 0), (None, [], 0), (None, [], 1)])
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            real_inv = A.invalidate_perm_cache
            s.enter_context(patch("src.web_api.auth.invalidate_perm_cache", side_effect=real_inv))
            perms_mod._PERM_CACHE.update(at=0.0, roles={"ops": {"read", "trade"}}, users={})   # 预置缓存=已删组的旧权限
            r = _client().delete("/api/user-groups/5", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        assert perms_mod._PERM_CACHE["roles"] is None   # 缓存真被清（非 mock 代理断言）
        # 重建同名组后加载：permission 表空（mock 空表回退）→ PERMISSIONS 字典无 ops → 零权限不继承
        perms_mod._PERM_CACHE.update(at=0.0, roles=None, users={})
        with patch("src.data_platform.db.get_conn", return_value=_conn()):
            roles = perms_mod.load_role_permissions()
        assert "ops" not in roles

    def test_disabled_account_still_401(self):
        """require_authenticated 不放宽账号状态——verify_jwt fail-closed 保留。"""
        import contextlib
        from fastapi import HTTPException
        with contextlib.ExitStack() as s:
            s.enter_context(patch("src.web_api.auth.verify_jwt",
                                  side_effect=HTTPException(401, "账号已禁用或注销")))
            r = _client().get("/api/auth/me", headers={"Authorization": "Bearer t"})
        assert r.status_code == 401


class TestUserRoleDynamic:
    def test_create_user_ghost_role(self):
        import contextlib
        conn = _conn(scripted=[(None, [], 0)])
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            r = _client().post("/api/user", json={"username": "u", "password": "Abcd1234", "role": "ghost"}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 400 and r.json()["code"] == "ROLE_INVALID"

    def test_update_user_ghost_role(self):
        import contextlib
        conn = _conn(scripted=[(("u",), [], 0), (None, [], 0)])   # SELECT username → 组校验 None
        with contextlib.ExitStack() as s:
            for p in _admin_ctx(conn): s.enter_context(p)
            s.enter_context(patch("src.web_api.routes.auth_routes.guard_user_mutation"))
            r = _client().post("/api/user/9?role=ghost", headers={"Authorization": "Bearer t"})
        assert r.status_code == 400 and r.json()["code"] == "ROLE_INVALID"


class TestGetPermissionsFallback:
    def test_empty_group_table_falls_back_to_builtin(self):
        """user_group 空表 → 角色清单回退四内置（盲审 B P2-3，防未迁移环境静默半瘫）。"""
        from src.web_api.routes.auth_routes import _all_group_names
        conn = _conn(scripted=[(None, [], 0)])   # SELECT name 空
        with patch("src.data_platform.db.get_conn", return_value=conn):
            names = _all_group_names()
        assert names == ["admin", "trader", "analyst", "viewer"]
