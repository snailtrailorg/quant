"""安全评估 #3（2026-10-11）：登录失败双钥限速（u: + ip:，只记失败）。

设计裁定（用户）：ip 桶须防「局域网连坐」——只计失败 + 高阈值（100/时），
成功登录清用户桶且不计 ip 桶。Valkey 存储（重启不清零），降级内存 fail-open。
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean_buckets():
    """测试隔离：Valkey 键 TTL 1h 跨运行持久——每用例前后清 loginfail:*。"""
    yield
    try:
        import redis
        r = redis.Redis(decode_responses=True)
        for k in list(r.scan_iter("loginfail:*")):
            r.delete(k)
    except Exception:   # noqa: S110  无 redis 环境（CI）时静默——桶已在内存层隔离
        pass


def _client():
    from src.web_api.main import app
    return TestClient(app, raise_server_exceptions=False)


def _auth_patch(user=None):
    """verify_jwt 正常 + authenticate 返回 user（None=坏凭证）。"""
    from contextlib import ExitStack
    es = ExitStack()
    es.enter_context(patch("src.web_api.auth.verify_jwt",
                           return_value={"sub": 1, "username": "t", "role": "admin", "db_role": "admin"}))
    es.enter_context(patch("src.web_api.routes.auth_routes.authenticate", return_value=user))
    return es


class TestDualKeyFailLimit:
    def test_fail_only_counting(self):
        """失败才计数：10 次失败后第 11 次 429（u: 桶）。"""
        c = _client()
        with _auth_patch(user=None):
            for i in range(10):
                r = c.post("/api/auth/login", json={"username": "victim", "password": "w"})
                assert r.status_code == 401, i
            r11 = c.post("/api/auth/login", json={"username": "victim", "password": "w"})
            assert r11.status_code == 429

    def test_success_not_counted_no_lockout(self):
        """连坐解除：成功登录不计数——50 次成功后仍 200（原实现会 429）。"""
        user = {"id": "1", "username": "ok", "role": "viewer"}
        c = _client()
        with _auth_patch(user=user):
            for i in range(50):
                r = c.post("/api/auth/login", json={"username": "okuser", "password": "right"})
                assert r.status_code == 200, f"第 {i} 次成功登录被拒——连坐回归"

    def test_success_clears_user_bucket(self):
        """成功登录清 u: 桶：9 失败 + 1 成功 + 再 9 失败 → 仍 401 非 429。"""
        c = _client()
        with _auth_patch(user=None):
            for _ in range(9):
                c.post("/api/auth/login", json={"username": "resetme", "password": "w"})
        with _auth_patch(user={"id": "2", "username": "resetme", "role": "viewer"}):
            assert c.post("/api/auth/login", json={"username": "resetme", "password": "r"}).status_code == 200
        with _auth_patch(user=None):
            for _ in range(9):
                r = c.post("/api/auth/login", json={"username": "resetme", "password": "w"})
                assert r.status_code == 401   # 未 429=桶已清

    def test_ip_bucket_spray_blocked_high_threshold(self):
        """喷洒防护：同 IP 换用户名失败 100 次后，第 101 个（新用户名）也 429。"""
        c = _client()
        with _auth_patch(user=None), \
             patch("src.web_api.routes.auth_routes._login_ip", return_value="1.2.3.4"):
            for i in range(100):
                c.post("/api/auth/login", json={"username": f"spray{i}", "password": "w"})
            r = c.post("/api/auth/login", json={"username": "freshname", "password": "w"})
            assert r.status_code == 429

    def test_ip_bucket_normal_office_unaffected(self):
        """办公室共享出口：同 IP 99 次失败内，正常用户登录不受影响。"""
        user = {"id": "3", "username": "fine", "role": "viewer"}
        c = _client()
        with patch("src.web_api.routes.auth_routes._login_ip", return_value="5.6.7.8"):
            with _auth_patch(user=None):
                for i in range(99):   # 同事 A 喷洒 99 次（未到 100 阈值）
                    c.post("/api/auth/login", json={"username": f"other{i}", "password": "w"})
            with _auth_patch(user=user):
                r = c.post("/api/auth/login", json={"username": "colleague", "password": "ok"})
                assert r.status_code == 200, "99 次他人失败即拦正常用户——阈值回归"

    def test_valkey_down_failopen(self):
        """Valkey 挂：降级内存桶仍工作（fail-open 到原 dict 语义），不 500。"""
        c = _client()
        def _boom(*a, **k):
            raise ConnectionError("valkey down")
        with _auth_patch(user=None), \
             patch("src.web_api.routes.auth_routes._fail_count", side_effect=_boom), \
             patch("src.web_api.routes.auth_routes._record_fail", side_effect=_boom), \
             patch("src.web_api.routes.auth_routes._clear_fails", side_effect=_boom):
            r = c.post("/api/auth/login", json={"username": "x", "password": "y"})
            assert r.status_code == 401   # 计数面全坏——认证路径不受影响
