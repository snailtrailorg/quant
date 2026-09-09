"""批11 用户管理页测试（八步法步 5；盲审 P1-2）。

覆盖新增面：①batch-delete 端点（参数校验三档+真删+审计）②注册保留字/空用户名
③注册头像降级路径（P1-1：账号已建后头像失败不阻断）④_save_avatar_base64
（好图落盘/坏 base64/超限）⑤昵称 20 上限入 SQL。
"""
from base64 import b64decode
from unittest.mock import patch, MagicMock

import pytest


def _admin_conn(fetchall_rows=None, rowcount=0):
    """admin 身份 + 可控行为的 conn。verify_jwt 打桩身份层（同 test_permissions_w4 模式）。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchall.return_value = fetchall_rows or []
    cur.rowcount = rowcount
    conn.execute.return_value = cur
    return conn


def _client():
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    return TestClient(app)


ADMIN = {"sub": "1", "username": "admin", "role": "admin", "db_role": "admin"}


class TestBatchDelete:
    """POST /api/invites/batch-delete：参数校验三档 + 真删 + 审计记 ids（P2-1/P2-8）。"""

    def _post(self, body, conn=None):
        # 双 patch：auth_routes 顶部 `from src.data_platform.db import get_conn` 是模块级绑定
        # （只 patch 定义处时端点函数体走真库——首跑 deleted=0 即此因）；require_perm 层走定义处
        with patch("src.web_api.auth.verify_jwt", return_value=ADMIN), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=conn or _admin_conn()), \
             patch("src.data_platform.db.get_conn", return_value=conn or _admin_conn()), \
             patch("src.web_api.routes.auth_routes.audit_log") as au:
            r = _client().post("/api/invites/batch-delete", json=body,
                               headers={"Authorization": "Bearer t"})
        return r, au

    def test_empty_ids(self):
        r, _ = self._post({"ids": []})
        assert r.status_code == 400 and r.json()["code"] == "IDS_EMPTY"

    def test_non_int_ids(self):
        r, _ = self._post({"ids": ["a"]})
        assert r.status_code == 400 and r.json()["code"] == "IDS_INVALID"
        r, _ = self._post({"ids": [1.5]})
        assert r.status_code == 400 and r.json()["code"] == "IDS_INVALID"

    def test_over_100(self):
        r, _ = self._post({"ids": list(range(101))})
        assert r.status_code == 400 and r.json()["code"] == "TOO_MANY"

    def test_delete_count_and_audit_ids(self):
        conn = _admin_conn(rowcount=2)
        r, au = self._post({"ids": [3, 7]}, conn)
        assert r.status_code == 200 and r.json() == {"deleted": 2}
        sql = conn.execute.call_args[0][0]
        assert "ANY(%s)" in sql and "type='invite'" in sql      # 参数化+invite 过滤（保 reset token）
        # 审计 detail 带 ids（P2-8：不可逆删除可追溯）
        detail = au.call_args[0][-1]
        assert "3" in str(detail) and "7" in str(detail)

    def test_viewer_forbidden(self):
        viewer = dict(ADMIN, role="viewer", db_role="viewer")
        with patch("src.web_api.auth.verify_jwt", return_value=viewer), \
             patch("src.data_platform.db.get_conn", return_value=_admin_conn()):
            r = _client().post("/api/invites/batch-delete", json={"ids": [1]},
                               headers={"Authorization": "Bearer t"})
        assert r.status_code == 403


class TestRegisterValidation:
    """注册用户名防线：保留字（批11）+ strip 后空（P2-3）——不触 DB 即 400。"""

    def _post(self, username):
        with patch("src.web_api.routes.auth_routes.register_user") as ru:
            r = _client().post("/api/auth/register",
                               json={"token": "t", "username": username, "password": "Abcd1234"})
        return r, ru

    def test_reserved(self):
        for name in ("admin", "Admin", "ADMIN", " root "):
            r, ru = self._post(name)
            assert r.status_code == 400 and r.json()["code"] == "USERNAME_RESERVED", name
            ru.assert_not_called()

    def test_blank(self):
        r, ru = self._post("   ")
        assert r.status_code == 400 and r.json()["code"] == "USERNAME_EMPTY"
        ru.assert_not_called()


class TestRegisterAvatar:
    """注册头像：好图落盘；坏数据降级不阻断（P1-1——账号已建+token 已烧）。"""

    @staticmethod
    def _png_b64():
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "PNG")
        import base64
        return base64.b64encode(buf.getvalue()).decode()

    def test_save_avatar_ok(self, tmp_path):
        from src.web_api.routes.auth_routes import _save_avatar_base64
        with patch("src.web_api.routes.auth_routes._AVATAR_DIR", tmp_path):
            url = _save_avatar_base64(self._png_b64(), 99)
        assert "user_99.jpg" in url
        f = tmp_path / "user_99.jpg"
        assert f.exists() and f.stat().st_size > 0

    def test_save_avatar_bad_b64(self, tmp_path):
        from src.web_api.routes.auth_routes import _save_avatar_base64, ApiError
        with patch("src.web_api.routes.auth_routes._AVATAR_DIR", tmp_path):
            with pytest.raises(ApiError):
                _save_avatar_base64("!!!not-base64!!!", 99)

    def test_save_avatar_too_large(self, tmp_path):
        from src.web_api.routes.auth_routes import _save_avatar_base64, ApiError
        import base64
        big = base64.b64encode(b"x" * (3 * 1024 * 1024)).decode()
        with patch("src.web_api.routes.auth_routes._AVATAR_DIR", tmp_path):
            with pytest.raises(ApiError) as e:
                _save_avatar_base64(big, 99)
            assert e.value.status_code == 400

    def test_register_survives_avatar_failure(self):
        """P1-1 核心：register_user 成功后头像抛错 → 注册仍 200（降级跳过）。"""
        from src.web_api.routes.auth_routes import ApiError
        with patch("src.web_api.routes.auth_routes.register_user",
                   return_value={"id": 9, "username": "u9", "email": "e@x.dev"}), \
             patch("src.web_api.routes.auth_routes._save_avatar_base64",
                   side_effect=ApiError(400, "AVATAR_FORMAT", "x")), \
             patch("src.web_api.routes.auth_routes.send_activation_email"), \
             patch("src.web_api.routes.auth_routes.audit_log"):
            r = _client().post("/api/auth/register",
                               json={"token": "t", "username": "u9", "password": "Abcd1234",
                                     "nickname": "nick", "avatar": "data:image/gif;base64,R0lGOD"})
        assert r.status_code == 200 and r.json()["status"] == "registered"

    def test_register_passes_nickname(self):
        """昵称透传 register_user（20 上限在 SQL LEFT）。"""
        with patch("src.web_api.routes.auth_routes.register_user",
                   return_value={"id": 9, "username": "u9", "email": "e@x.dev"}) as ru, \
             patch("src.web_api.routes.auth_routes.send_activation_email"), \
             patch("src.web_api.routes.auth_routes.audit_log"):
            _client().post("/api/auth/register",
                           json={"token": "t", "username": "u9", "password": "Abcd1234",
                                 "nickname": "  昵称  "})
        assert ru.call_args.kwargs.get("nickname") == "昵称"     # strip 后入库


class TestRegisterUserNicknameSql:
    """auth.register_user：昵称 LEFT(...,20) 入 SQL（P2-4）。"""

    def test_nickname_left20_in_sql(self):
        import src.web_api.auth as A
        conn = _admin_conn()
        with patch.object(A, "verify_token", return_value={"id": 5, "email": "e@x.dev"}), \
             patch.object(A, "create_user", return_value=42), \
             patch.object(A, "_mark_token_used"), \
             patch.object(A, "get_conn", return_value=conn):
            A.register_user("tok", "u1", "Abcd1234", nickname="n" * 50)
        sql = conn.execute.call_args[0][0]
        assert "LEFT(COALESCE(NULLIF(%s,''), nickname), 20)" in sql
        assert "email_verified" not in sql                       # 列已删（迁移 0070）
