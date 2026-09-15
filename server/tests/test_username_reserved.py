"""批26-8：保留字黑名单加固——NFKC 归一化（防全角 ａｄｍｉｎ 绕过）+ 词表补"官方客服" +
admin 建用户路径同闸（原无校验）。"""
import contextlib
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


def test_nfkc_fullwidth_admin_rejected():
    """全角 ａｄｍｉｎ → NFKC 归一化后=admin → 拒（批11A 盲审 P2-11 主绕过路径）。"""
    from src.web_api.routes.auth_routes import _username_reserved
    assert _username_reserved("ａｄｍｉｎ") is True
    assert _username_reserved("ＡＤＭＩＮ") is True


def test_new_words_and_normal_names():
    from src.web_api.routes.auth_routes import _username_reserved
    assert _username_reserved("官方客服") is True       # 批26-8 补词
    assert _username_reserved(" admin ") is True         # strip + lower
    assert _username_reserved("admin2") is False         # 前缀正常名放行（不做模糊）
    assert _username_reserved("bernard") is False


def test_admin_create_user_same_gate():
    """admin 建用户同闸（批26-8：原路径无保留字校验——与注册路径一致性）。"""
    ADMIN = {"sub": "1", "username": "admin", "role": "admin", "db_role": "admin"}
    conn = MagicMock(); conn.__enter__.return_value = conn
    with contextlib.ExitStack() as s:
        s.enter_context(patch("src.web_api.auth.verify_jwt", return_value=ADMIN))
        s.enter_context(patch("src.web_api.routes.auth_routes.get_conn", return_value=conn))
        r = TestClient(_app()).post("/api/user", headers={"Authorization": "Bearer t"},
                                    json={"username": "ｒｏｏｔ", "password": "Passw0rd!234",
                                          "role": "viewer"})
    assert r.status_code == 400 and r.json()["code"] == "USERNAME_RESERVED"
    assert not conn.execute.called, "闸应在 group 查询前拒绝"


def _app():
    from src.web_api.main import app
    return app
