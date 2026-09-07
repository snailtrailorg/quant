"""DataSource 参数/熔断/限速覆写端点测试（盲审 A-P1-2 恢复：删积分档时连带误删的与积分档无关测试）。"""
import json
from unittest.mock import patch

import pytest

from src.data_platform import rate_limit
from src.data_platform.rate_limit import (
    CircuitBreaker, CircuitOpenError, rate_limit_context)
from src.data_platform.data_source import TushareDataSource


def _ds(params: dict | None = None) -> TushareDataSource:
    return TushareDataSource(params=json.dumps(params) if params else None)


@pytest.fixture(autouse=True)
def _clean_registries():
    rate_limit.reset_registries()
    yield
    rate_limit.reset_registries()


class TestGetParam:

    def test_nested_path_read(self):
        ds = _ds({"circuit_breaker": {"fail_threshold": 8, "reset_timeout": 120}})
        assert ds.get_param("circuit_breaker", "fail_threshold") == 8
        assert ds.get_param("circuit_breaker", "reset_timeout") == 120

    def test_missing_path_returns_default(self):
        ds = _ds({"circuit_breaker": {"fail_threshold": 8}})
        assert ds.get_param("circuit_breaker", "reset_timeout", default=60) == 60
        assert ds.get_param("no_such_ns", "key", default="dft") == "dft"
        assert ds.get_param("circuit_breaker", "deep", "deeper", default=1) == 1

    def test_explicit_none_returns_default(self):
        ds = _ds({"x": None})
        assert ds.get_param("x", default=42) == 42

    def test_get_param_float_clamps_hi(self):
        ds = _ds({"x": 500})
        assert ds.get_param_float("x", default=5.0, lo=1.0, hi=100.0) == 100.0

    def test_get_param_float_clamps_lo(self):
        ds = _ds({"x": 0})
        assert ds.get_param_float("x", default=5.0, lo=1.0, hi=100.0) == 1.0

    def test_get_param_float_invalid_returns_default(self):
        ds = _ds({"x": "not-a-number"})
        assert ds.get_param_float("x", default=5.0, lo=1.0, hi=100.0) == 5.0

    def test_get_param_float_in_range_untouched(self):
        ds = _ds({"x": "7.5"})
        assert ds.get_param_float("x", default=5.0, lo=1.0, hi=100.0) == 7.5


class TestCircuitBreakerFromDs:

    def test_reads_params_from_ds(self):
        ds = _ds({"circuit_breaker": {"fail_threshold": 8, "reset_timeout": 120}})
        cb = CircuitBreaker(ds=ds)
        assert cb._fail_threshold == 8
        assert cb._reset_timeout == 120.0

    def test_defaults_when_ds_param_absent(self):
        cb = CircuitBreaker(ds=_ds({}))
        assert cb._fail_threshold == 5
        assert cb._reset_timeout == 60.0

    def test_explicit_args_beat_ds_params(self):
        ds = _ds({"circuit_breaker": {"fail_threshold": 8}})
        cb = CircuitBreaker(fail_threshold=2, ds=ds)
        assert cb._fail_threshold == 2

    def test_ds_without_get_param_skipped(self):
        class _Bare:
            provider = "bare"
        cb = CircuitBreaker(ds=_Bare())
        assert cb._fail_threshold == 5
        assert cb._reset_timeout == 60.0

    def test_context_wires_ds_into_breaker(self):
        ds = _ds({"circuit_breaker": {"fail_threshold": 1}})
        with pytest.raises(ValueError):
            with rate_limit_context(ds, "ghost_api"):
                raise ValueError("boom")
        with pytest.raises(CircuitOpenError):
            with rate_limit_context(ds, "ghost_api"):
                pass


# --- rate-limit-override 端点（去积分档后仍存活） ---

class _FakeConn:
    def __init__(self, row):
        self._row = row
        self.updates: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        if sql.startswith("UPDATE"):
            self.updates.append((sql, args))
        cur = type("C", (), {"fetchone": lambda c: self._row})()
        return cur

    def commit(self):
        pass


@pytest.fixture
def admin_client():
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    from src.web_api import auth as _auth
    with patch.object(_auth, "verify_jwt",
                      return_value={"sub": "1", "username": "admin", "role": "admin"}):
        client = TestClient(app)
        client.headers.update({"Authorization": "Bearer test-token"})
        yield client


class _ConnPatch:
    def __init__(self, row):
        import src.web_api.routes.mgmt as mgmt
        self.conn = _FakeConn(row)
        self._p = patch.object(mgmt, "get_conn", lambda: self.conn)

    def __enter__(self):
        self._p.start()
        return self.conn

    def __exit__(self, *a):
        self._p.stop()
        return False


class TestRateLimitOverrideEndpoint:

    def test_post_override_null_deletes_override(self, admin_client):
        """value=null 删除覆写：rate_limits 清键，生效值回落类默认（stk_mins=3600）。"""
        row = (5, json.dumps({"rate_limits": {"stk_mins": 60}}))
        with _ConnPatch(row) as conn:
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"api_name": "stk_mins", "value": None})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["effective"] == 3600.0      # 回落类默认（去积分档后）
        saved = json.loads(conn.updates[0][1][0])
        assert "stk_mins" not in saved["rate_limits"]

    def test_post_override_value_out_of_range_rejected(self, admin_client):
        row = (5, json.dumps({}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"api_name": "daily", "value": 90000})
        assert r.status_code == 400
        assert r.json()["code"] == "OVERRIDE_VALUE_INVALID"

    def test_post_override_negative_rejected(self, admin_client):
        row = (5, json.dumps({}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"api_name": "daily", "value": -1})
        assert r.status_code == 400
        assert r.json()["code"] == "OVERRIDE_VALUE_INVALID"

    def test_post_circuit_breaker_saved_merged(self, admin_client):
        row = (5, json.dumps({"circuit_breaker": {"fail_threshold": 8}}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"circuit_breaker": {"reset_timeout": 120}})
        assert r.status_code == 200
        cb = r.json()["circuit_breaker"]
        assert cb == {"fail_threshold": 8, "reset_timeout": 120.0}

    def test_post_circuit_breaker_invalid_rejected(self, admin_client):
        row = (5, json.dumps({}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"circuit_breaker": {"fail_threshold": 0}})
        assert r.status_code == 400
        assert r.json()["code"] == "CB_VALUE_INVALID"

    def test_post_no_body_fields_rejected(self, admin_client):
        row = (5, json.dumps({}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override", json={})
        assert r.status_code == 400
        assert r.json()["code"] == "OVERRIDE_VALUE_INVALID"

    def test_get_rate_limits_unknown_provider_404(self, admin_client):
        r = admin_client.get("/api/datasource/no_such_provider/rate-limits")
        assert r.status_code == 404
        assert r.json()["code"] == "DS_NOT_REGISTERED"
