"""批20 · 个人中心信息扩展单测（20A profile 扩列 / 20B market_op / 20C 邮箱修改单事务）。

覆盖（方案 v2 双盲审 A/B 吸收后契约）：
- profile：扩列返回形态（created_at/last_login/ip/deactivated 映射）+ market_op 五键（DB role 源）
- email-change：密码错拒 / 同邮箱拒 / 他人占用拒 / 频控桶 / 成功发 token+后台邮件
- confirm：单事务四防线——占用复核 409 / 软删守卫 410（脱敏 NULL 不回写）/ token 原子标记（重放拒）
  / 成功改库+audit / 23505 竞态转 409 / user_id 缺失拒
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from src.web_api.main import app
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    """鉴权端点测试（health_monitor 同款）：patch verify_jwt 伪造 payload + Bearer 头
    （require_authenticated 是闭包，module 属性 patch 对不上——走真实链路+假 JWT）。"""
    from src.web_api import auth as _auth
    with patch.object(_auth, "verify_jwt",
                      return_value={"sub": 1, "username": "u1", "role": "trader", "db_role": "trader"}):
        client.headers.update({"Authorization": "Bearer test-token"})
        yield client
        client.headers.pop("Authorization", None)
        from src.web_api.routes import auth_routes as _ar
        _ar._RATE_LIMITS.clear()   # emailchg 桶 3/3600 进程级——测试间清零防第 4 发 429


def _row(username="u1", email="u1@x.com", ph="hash", enabled=True, deleted=None):
    """profile SELECT 行（10 列）。"""
    return (username, "nick", "trader", None, email,
            datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 9, 13, tzinfo=timezone.utc),
            "1.2.3.4", enabled, deleted)


class TestProfileExpansion:
    def test_profile_returns_expanded_fields_and_market_op(self, authed_client):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.side_effect = [
            MagicMock(fetchone=lambda: _row()),                    # profile 主行
            MagicMock(fetchone=lambda: True),                       # market_op astock
            MagicMock(fetchone=lambda: False),                      # convertible
            MagicMock(fetchone=lambda: True), MagicMock(fetchone=lambda: True),
            MagicMock(fetchone=lambda: True),
        ]
        with patch("src.web_api.routes.auth_routes.get_conn", return_value=conn), \
             patch("src.web_api.routes.auth_routes.require_authenticated",
                   return_value={"sub": 1, "username": "u1", "db_role": "trader"}), \
             patch("src.data_platform.perms.market_op_allowed",
                   side_effect=lambda u, r, m: m in ("astock", "etf", "binance_perp", "okx_perp")):
            r = authed_client.get("/api/user/profile")
        assert r.status_code == 200
        d = r.json()
        assert d["created_at"] == "2026-01-01" and d["last_login_at"].startswith("2026-09-13")
        assert d["last_login_ip"] == "1.2.3.4" and d["deactivated"] is False
        assert d["market_op"] == {"astock": True, "convertible": False, "etf": True,
                                  "binance_perp": True, "okx_perp": True}

    def test_market_op_role_uses_db_role(self, client):
        """JWT 陈旧 role 不漂移（盲审A-P2-6）：以 db_role 判（自带 verify_jwt 桩注入 viewer）。"""
        from src.web_api import auth as _auth
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.side_effect = [MagicMock(fetchone=lambda: _row())] + \
            [MagicMock(fetchone=lambda: None)] * 5
        calls = []
        with patch.object(_auth, "verify_jwt",
                          return_value={"sub": 1, "username": "u1", "role": "admin", "db_role": "viewer"}), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=conn), \
             patch("src.data_platform.perms.market_op_allowed",
                   side_effect=lambda u, r, m: calls.append(r) or False):
            client.headers.update({"Authorization": "Bearer t"})
            client.get("/api/user/profile")
            client.headers.pop("Authorization", None)
        assert calls and set(calls) == {"viewer"}




def _mk_conn(side_effect_rows):
    """email-change 系 mock 连接：__exit__ 必须 falsy（真值吞异常→落真库调用=挂起根因）。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    if isinstance(side_effect_rows, list):
        conn.execute.side_effect = [MagicMock(fetchone=lambda r=r: r) for r in side_effect_rows]
    else:
        conn.execute.return_value = MagicMock(fetchone=lambda: side_effect_rows)
    return conn


class TestEmailChange:

    def test_wrong_password_rejected(self, authed_client):
        conn = _mk_conn(("u1@x.com", "hash"))
        with patch("src.web_api.routes.auth_routes.get_conn", return_value=conn), \
             patch("src.web_api.auth.verify_password", return_value=False):
            r = authed_client.post("/api/user/email-change",
                            json={"new_email": "new@x.com", "current_password": "bad"})
        assert r.status_code == 400 and r.json().get("code") == "OLD_PASSWORD_WRONG"

    def test_same_as_current_rejected(self, authed_client):
        conn = _mk_conn(("same@x.com", "hash"))
        with patch("src.web_api.routes.auth_routes.get_conn", return_value=conn), \
             patch("src.web_api.auth.verify_password", return_value=True):
            r = authed_client.post("/api/user/email-change",
                            json={"new_email": "SAME@X.com", "current_password": "ok"})
        # lower 规范化后与当前同 → SAME_AS_CURRENT（不区分大小写）
        assert r.status_code == 400 and r.json().get("code") == "SAME_AS_CURRENT"

    def test_occupied_rejected(self, authed_client):
        conn = _mk_conn([("u1@x.com", "hash"), (1,)])   # 主行 + 占用命中
        with patch("src.web_api.routes.auth_routes.get_conn", return_value=conn), \
             patch("src.web_api.auth.verify_password", return_value=True):
            r = authed_client.post("/api/user/email-change",
                            json={"new_email": "taken@x.com", "current_password": "ok"})
        assert r.status_code == 409 and r.json().get("code") == "EMAIL_TAKEN"

    def test_success_creates_token_and_sends(self, authed_client):
        conn = _mk_conn([("u1@x.com", "hash"), None])
        with patch("src.web_api.routes.auth_routes.get_conn", return_value=conn), \
             patch("src.web_api.auth.verify_password", return_value=True), \
             patch("src.web_api.auth.create_token", return_value="tok") as ct, \
             patch("src.web_api.routes.auth_routes.send_email_change_email") as send:
            r = authed_client.post("/api/user/email-change",
                            json={"new_email": "New@X.com", "current_password": "ok", "lang": "zh"})
        assert r.status_code == 200 and r.json() == {"status": "sent"}
        ct.assert_called_once_with("new@x.com", "email_change", user_id=1, hours=1)   # lower 规范化
        assert send.called   # 后台任务已排队（TestClient 同步执行 background tasks）


class TestEmailChangeConfirm:
    def _t(self):
        return {"id": 77, "user_id": 1, "email": "new@x.com"}

    def test_occupied_window_409(self, client):
        conn = _mk_conn([(1,)])   # 复核占用=有
        with patch("src.web_api.auth.verify_token", return_value=self._t()), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=conn):
            r = client.post("/api/user/email-change/confirm", json={"token": "tok"})
        assert r.status_code == 409 and r.json().get("code") == "EMAIL_TAKEN"
        conn.commit.assert_not_called()   # 库零触碰

    def test_soft_deleted_guard_410_no_writeback(self, client):
        """发起后被注销：UPDATE WHERE 守卫 rowcount=0 → 410；NULL 脱敏邮箱不回写（盲审A-P1-1）。"""
        conn = _mk_conn([None, ("old@x.com",), None])   # 占用空/旧行/UPDATE RETURNING 空
        with patch("src.web_api.auth.verify_token", return_value=self._t()), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=conn):
            r = client.post("/api/user/email-change/confirm", json={"token": "tok"})
        assert r.status_code == 410 and r.json().get("code") == "ACCOUNT_GONE"
        conn.commit.assert_not_called()

    def test_replay_rejected_atomic_mark(self, client):
        """并发双 confirm：后到者 used=true → 幂等拒（盲审A-P2-3 原子标记）。"""
        conn = _mk_conn([None, ("old@x.com",), ("u1",), None])   # 占用空/旧行/UPDATE 成功/标记失败
        with patch("src.web_api.auth.verify_token", return_value=self._t()), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=conn):
            r = client.post("/api/user/email-change/confirm", json={"token": "tok"})
        assert r.status_code == 400
        conn.commit.assert_not_called()

    def test_success_updates_and_audits(self, client):
        conn = _mk_conn([None, ("old@x.com",), ("u1",), (77,)])
        with patch("src.web_api.auth.verify_token", return_value=self._t()), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=conn), \
             patch("src.web_api.routes.auth_routes.audit_log") as al:
            r = client.post("/api/user/email-change/confirm", json={"token": "tok"})
        assert r.status_code == 200 and r.json()["email"] == "new@x.com"
        conn.commit.assert_called_once()
        al.assert_called_once()
        assert al.call_args[0][0] == "u1" and al.call_args[0][2] == "email_change" \
               and "old@x.com->new@x.com" in al.call_args[0][3]   # 盲审B-P3-6：变更入 detail 位

    def test_token_without_user_id_rejected(self, client):
        """invite 场景 token 无 user_id——防串用（盲审A-P3）。"""
        with patch("src.web_api.auth.verify_token", return_value={"id": 1, "user_id": None, "email": "x@x.com"}):
            r = client.post("/api/user/email-change/confirm", json={"token": "tok"})
        assert r.status_code == 400


class TestConfirmEdge:
    """盲审A-P2-1：第四防线（23505→409）与 token 无效态零覆盖补齐。"""

    def _t(self):
        return {"id": 77, "user_id": 1, "email": "new@x.com"}

    def test_token_invalid_expired_400(self, client):
        """verify_token 返回 None（used/expired/错 kind/不存在同语义）→ 400 库零触碰。"""
        with patch("src.web_api.auth.verify_token", return_value=None):
            r = client.post("/api/user/email-change/confirm", json={"token": "tok"})
        assert r.status_code == 400 and r.json().get("code") == "TOKEN_INVALID_OR_EXPIRED"

    def test_unique_violation_races_to_409(self, client):
        """窗口竞态：UPDATE 撞唯一约束（psycopg 裸异常带 pgcode 23505）→ 409 非 500。"""
        class _UniqueViolation(Exception):
            def __init__(self):
                self.orig = MagicMock(pgcode="23505")
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False
        conn.execute.side_effect = [
            MagicMock(fetchone=lambda: None),          # 占用空（发起时无冲突）
            _UniqueViolation(),                         # UPDATE users 撞唯一约束
        ]
        with patch("src.web_api.auth.verify_token", return_value=self._t()), \
             patch("src.web_api.routes.auth_routes.get_conn", return_value=conn):
            r = client.post("/api/user/email-change/confirm", json={"token": "tok"})
        assert r.status_code == 409 and r.json().get("code") == "EMAIL_TAKEN"
        conn.commit.assert_not_called()


class TestEmailTemplate:
    """盲审B-P0：改邮箱邮件 URL 必须真实渲染（{{url}} 撞 .format 转义=字面 {url} 的历史坑）。"""

    def test_url_rendered_not_literal(self):
        from src.email_service import EMAIL_CHANGE_TPL, _render
        for lang in ("zh", "en"):
            subject, body = _render(EMAIL_CHANGE_TPL, lang, url="http://x/email-confirm?token=T")
            assert "http://x/email-confirm?token=T" in body, f"{lang} body 未渲染 url"
            assert "{url}" not in body, f"{lang} body 含字面 {{url}}（.format 转义坑）"
            assert "{btn_red}" not in body
