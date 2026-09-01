"""批次A 快赢四小修单测：邮箱登录 / 禁用区分 / jti 黑名单 / last_login 迁移存在。"""
from unittest.mock import patch, MagicMock
import pytest

from src.web_api import auth as auth_mod
from src.web_api.errors import ApiError


def _auth_conn(row):
    """mock get_conn：execute 返回固定行。row=None 表示无用户。"""
    m = MagicMock()
    m.__enter__.return_value = m
    cur = MagicMock()
    cur.fetchone.return_value = row
    m.execute.return_value = cur
    return m


def test_authenticate_email_lookup():
    """含 @ 按 email 查（SQL 字段选择），密码对返回用户。"""
    row = (1, "alice", "$2b$hash", "viewer", True)
    with patch.object(auth_mod, "get_conn", return_value=_auth_conn(row)), \
         patch.object(auth_mod, "verify_password", return_value=True):
        u = auth_mod.authenticate("a@x.com", "pw12345")
    assert u == {"id": 1, "username": "alice", "role": "viewer"}


def test_authenticate_username_lookup():
    """不含 @ 按 username 查。"""
    row = (1, "alice", "$2b$hash", "viewer", True)
    with patch.object(auth_mod, "get_conn", return_value=_auth_conn(row)), \
         patch.object(auth_mod, "verify_password", return_value=True):
        auth_mod.authenticate("alice", "pw12345")  # 不抛


def test_authenticate_disabled_raises_code():
    """密码对但 enabled=false → ApiError(ACCOUNT_DISABLED)，不再误报凭证错误。"""
    row = (1, "alice", "$2b$hash", "viewer", False)
    with patch.object(auth_mod, "get_conn", return_value=_auth_conn(row)), \
         patch.object(auth_mod, "verify_password", return_value=True):
        with pytest.raises(ApiError) as e:
            auth_mod.authenticate("alice", "pw12345")
    assert e.value.code == "ACCOUNT_DISABLED"


def test_authenticate_wrong_password_none():
    row = (1, "alice", "$2b$hash", "viewer", True)
    with patch.object(auth_mod, "get_conn", return_value=_auth_conn(row)), \
         patch.object(auth_mod, "verify_password", return_value=False):
        assert auth_mod.authenticate("alice", "bad") is None


def test_jwt_jti_and_blacklist():
    """create_jwt 带 jti；黑名单命中 → 401。SD1 后 verify_jwt 增加账号状态查询，需 mock。"""

    class UConn:  # F-45 账号状态检查：enabled 正常
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            class C:
                def fetchone(self):
                    return (True, None, "viewer")   # W4:+role 列
            return C()

    token = auth_mod.create_jwt("1", "alice", "viewer")
    with patch.object(auth_mod, "get_conn", lambda: UConn()):
        payload = auth_mod.verify_jwt(token)      # 未在黑名单 → 正常
        assert payload["jti"]
        # 加入黑名单后再验 → 401
        r = MagicMock()
        r.exists.return_value = 1
        with patch("redis.Redis.from_url", return_value=r):
            with pytest.raises(Exception) as e:
                auth_mod.verify_jwt(token)
            assert "登出" in str(e.value.detail) or e.value.status_code == 401


def test_revoke_jwt_sets_blacklist():
    """revoke_jwt 把 jti 写入黑名单（TTL=剩余寿命）。"""
    token = auth_mod.create_jwt("1", "alice", "viewer")
    r = MagicMock()
    with patch("redis.Redis.from_url", return_value=r):
        assert auth_mod.revoke_jwt(token) is True
    r.setex.assert_called_once()
    args = r.setex.call_args[0]
    assert args[0].startswith("jwt:bl:") and args[1] > 0
