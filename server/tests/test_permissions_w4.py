"""W4 权限三维化 C 阶段测试（2026-09-01）。

分层：①基线锁（现行为先钉死再重构的防线,盲审 B）②effective 合并序
③锁键双路径 ④override CRUD+自锁防线 ⑤GET 三维形状。
"""
from unittest.mock import patch, MagicMock

import src.web_api.auth as auth_mod


def _conn_rows(rows):
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = rows
    return conn


def _reset_cache():
    auth_mod.invalidate_perm_cache()


class TestBaseline:
    """先锁现行为（重构防线）。"""

    def test_role_fallback_dict_on_db_fail(self):
        with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db")):
            auth_mod._PERM_CACHE.update(at=0.0, roles=None, users={})
            roles = auth_mod.load_role_permissions()
        assert roles == auth_mod.PERMISSIONS          # fail→字典

    def test_role_table_overrides_dict(self):
        # 表有 role 行 → 全量以表为准（现 P1-4 语义）
        auth_mod._PERM_CACHE.update(at=0.0, roles=None, users={})
        rows = [("trader", "read", "allow"), ("trader", "trade", "allow")]
        with patch("src.data_platform.db.get_conn", return_value=_conn_rows(rows)):
            roles = auth_mod.load_role_permissions()
        assert roles["trader"] == {"read", "trade"}    # 不含字典的 halt 等


class TestEffective:
    """user deny > user allow > role allow（10 §3 合并序）。"""

    def test_no_user_rows_equals_role(self):
        auth_mod._PERM_CACHE.update(at=0.0, roles=None, users={})
        role_rows = [("trader", "read", "allow"), ("trader", "trade", "allow")]
        user_rows = []
        def gc():
            c = _conn_rows(role_rows if not TestEffective._user else user_rows)
            return c
        TestEffective._user = False
        with patch("src.data_platform.db.get_conn", side_effect=lambda: _conn_rows(role_rows)):
            base, _ = auth_mod.load_effective_permissions("", "trader")
        assert base == {"read", "trade"}

    _user = False

    def test_user_deny_beats_role_allow(self):
        auth_mod._PERM_CACHE.update(at=0.0, roles=None, users={})
        with patch("src.data_platform.db.get_conn",
                   side_effect=lambda: _conn_rows([("trader", "read", "allow"),
                                                   ("trader", "trade", "allow")] if not TestEffective._user
                                                  else [("trade", "deny")])):   # user 查询 2 列(resource,effect)
            # 先载 role 面
            TestEffective._user = False
            auth_mod.load_role_permissions()
            TestEffective._user = True
            perms, src = auth_mod.load_effective_permissions("bob", "trader")
        assert "trade" not in perms and "read" in perms
        assert src["__denied__"] == ["trade"]

    def test_user_allow_fills_role_gap(self):
        auth_mod._PERM_CACHE.update(at=0.0, roles=None, users={})
        with patch("src.data_platform.db.get_conn",
                   side_effect=lambda: _conn_rows([("viewer", "read", "allow")] if not TestEffective._user
                                                  else [("data_sync", "allow")])):   # 2 列
            TestEffective._user = False
            auth_mod.load_role_permissions()
            TestEffective._user = True
            perms, src = auth_mod.load_effective_permissions("bob", "viewer")
        assert perms == {"read", "data_sync"}
        assert src["data_sync"] == "user-override" and src["read"] == "role-base"

    def test_user_read_fail_failopen_role(self):
        auth_mod._PERM_CACHE.update(at=0.0, roles=None, users={})
        state = {"n": 0}
        def gc():
            state["n"] += 1
            if state["n"] == 1:
                return _conn_rows([("viewer", "read", "allow")])
            raise RuntimeError("db down at user query")
        with patch("src.data_platform.db.get_conn", side_effect=gc):
            perms, _ = auth_mod.load_effective_permissions("bob", "viewer")
        assert perms == {"read"}                        # user 维读失败=按角色


class TestLockedKeys:
    def test_locked_consts(self):
        assert auth_mod.LOCKED_PERM_KEYS == {"user_mgmt", "resume", "account_keys"}
        assert "system_config" in auth_mod.ADMIN_ROLE_FLOOR


class TestOverrideCrud:
    """POST /api/permissions/user/{username} + 锁键/自锁防线（mock DB）。"""

    def _call(self, username, body):
        from src.web_api.routes.auth_routes import update_user_override
        conn = MagicMock(); conn.__enter__.return_value = conn
        tconn = MagicMock(); tconn.__enter__.return_value = tconn
        tconn.execute.return_value.fetchone.return_value = ["admin"]
        def gc():
            return tconn if not TestOverrideCrud._phase else conn
        TestOverrideCrud._phase = False
        with patch("src.data_platform.db.get_conn", side_effect=gc), \
             patch("src.web_api.routes.auth_routes.audit_log"):
            return update_user_override(username, body, {"username": "op"})

    _phase = False

    def test_allow_deny_clear_roundtrip(self):
        r = self._call("bob", {"dimension": "api", "resource": "data_sync", "effect": "deny"})
        assert r["effect"] == "deny"
        r2 = self._call("bob", {"dimension": "api", "resource": "data_sync", "effect": "clear"})
        assert r2["effect"] == "clear"

    def test_locked_key_rejected(self):
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            self._call("bob", {"dimension": "api", "resource": "user_mgmt", "effect": "allow"})
        assert ei.value.status_code == 400

    def test_self_lock_guard_admin_deny(self):
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            self._call("adminbob", {"dimension": "api", "resource": "system_config", "effect": "deny"})
        assert ei.value.status_code == 400
