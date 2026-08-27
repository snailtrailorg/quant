"""积分档四层限流单测（2026-08-27，四层限流+积分档预设批次）。

覆盖：
- 四层解析全矩阵：L0 兜底 / L0+L1 档位预设 / L0+L1+L2 单参数覆写 / L0+L1+L2+L3 时段乘数
- points_tier 非法值（"abc"/999/缺省）回落 L0 不崩；字符串数字 "2000" 可解析
- 向后兼容：points_tier 不存在 = 老三级行为不变
- get_param 命名空间路径 / get_param_float 范围钳位
- CircuitBreaker 从 ds.params.circuit_breaker 读参数（含 rate_limit_context 接线）
- Web 端点：GET presets 结构 / POST tier 校验+diff / POST override 覆写与 null 删除 / 熔断参数
"""
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
    """context 注册表进程级——每测清零防熔断计数跨测污染（test_rate_limit 同款）。"""
    rate_limit.reset_registries()
    yield
    rate_limit.reset_registries()


# --- 四层解析全矩阵 ---

class TestFourLayerMatrix:

    def test_l0_default_alone(self):
        """L0：无任何 params → 类级 DEFAULT_RATE_LIMITS（= 200 积分现状实测值）。"""
        ds = _ds()
        assert ds.get_rate_limit("daily") == 0.5
        assert ds.get_rate_limit("stk_mins") == 3600.0
        assert ds.get_rate_limit("ghost_api") == 0.0   # 未知接口 0=不限

    def test_l0_l1_tier_preset(self):
        """L0+L1：points_tier=2000 → 预设批量覆盖（daily 0.2 / stk_mins 0.3 / adj_factor 0.15）。"""
        ds = _ds({"points_tier": 2000})
        assert ds.get_rate_limit("daily") == 0.2
        assert ds.get_rate_limit("stk_mins") == 0.3
        assert ds.get_rate_limit("adj_factor") == 0.15

    def test_l0_l1_l2_override(self):
        """L0+L1+L2：覆写只动显式键——stk_mins 0.25 生效，daily 仍走 L1 预设 0.2。"""
        ds = _ds({"points_tier": 2000, "rate_limits": {"stk_mins": 0.25}})
        assert ds.get_rate_limit("stk_mins") == 0.25
        assert ds.get_rate_limit("daily") == 0.2

    def test_l0_l1_l2_l3_full_stack(self):
        """四层全叠：L1 2000 档 → L2 覆写 stk_mins=0.25 → L3 ×2 → daily 0.1 / stk_mins 0.125。"""
        ds = _ds({"points_tier": 2000, "rate_limits": {"stk_mins": 0.25},
                  "rate_time_overrides": [{"window": "00:00-23:59", "multiplier": 2}]})
        assert abs(ds.get_rate_limit("daily") - 0.1) < 1e-9
        assert abs(ds.get_rate_limit("stk_mins") - 0.125) < 1e-9

    def test_tier_200_preset_equals_defaults(self):
        """200 档预设 = 代码默认（切换到 200 积分行为与现状完全一致）。"""
        ds = _ds({"points_tier": 200})
        for api, v in TushareDataSource.DEFAULT_RATE_LIMITS.items():
            assert ds.get_rate_limit(api) == v

    def test_tier_5000_preset(self):
        """5000 档：daily 0.1 / stk_mins 0.12 / adj_factor 0.06。"""
        ds = _ds({"points_tier": 5000})
        assert ds.get_rate_limit("daily") == 0.1
        assert ds.get_rate_limit("stk_mins") == 0.12
        assert ds.get_rate_limit("adj_factor") == 0.06


# --- points_tier 非法值防呆 ---

class TestTierInvalid:

    def test_tier_non_numeric_string_falls_to_l0(self):
        """tier="abc" → 跳过预设层回落 L0（不崩）。"""
        assert _ds({"points_tier": "abc"}).get_rate_limit("daily") == 0.5

    def test_tier_unknown_number_falls_to_l0(self):
        """tier=999（不在预设表）→ 跳过预设层回落 L0。"""
        assert _ds({"points_tier": 999}).get_rate_limit("daily") == 0.5

    def test_tier_numeric_string_parses(self):
        """tier="2000"（字符串数字，前端下拉可能存 str）→ int() 解析成功走预设。"""
        assert _ds({"points_tier": "2000"}).get_rate_limit("daily") == 0.2

    def test_tier_absent_legacy_three_levels(self):
        """向后兼容：points_tier 缺省 = 老三级行为（rate_limits 覆写 + 时段乘数照常）。"""
        ds = _ds({"rate_limits": {"daily": 1.0},
                  "rate_time_overrides": [{"window": "00:00-23:59", "multiplier": 2}]})
        assert abs(ds.get_rate_limit("daily") - 0.5) < 1e-9   # 与改造前三级解析同值

    def test_override_only_affects_specified_key(self):
        """L2 只影响指定键：覆写 daily=1.0 后 adj_factor 仍走 L1 预设（2000 档 0.15）。"""
        ds = _ds({"points_tier": 2000, "rate_limits": {"daily": 1.0}})
        assert ds.get_rate_limit("daily") == 1.0
        assert ds.get_rate_limit("adj_factor") == 0.15


# --- get_param / get_param_float 通用读取器 ---

class TestGetParam:

    def test_nested_path_read(self):
        """多层嵌套路径读取。"""
        ds = _ds({"circuit_breaker": {"fail_threshold": 8, "reset_timeout": 120}})
        assert ds.get_param("circuit_breaker", "fail_threshold") == 8
        assert ds.get_param("circuit_breaker", "reset_timeout") == 120

    def test_missing_path_returns_default(self):
        """路径不存在 / 中途非 dict → 回 default 不抛。"""
        ds = _ds({"circuit_breaker": {"fail_threshold": 8}})
        assert ds.get_param("circuit_breaker", "reset_timeout", default=60) == 60
        assert ds.get_param("no_such_ns", "key", default="dft") == "dft"
        assert ds.get_param("circuit_breaker", "deep", "deeper", default=1) == 1

    def test_explicit_none_returns_default(self):
        """值为显式 null → 回 default（None 与缺省同义）。"""
        ds = _ds({"x": None})
        assert ds.get_param("x", default=42) == 42

    def test_get_param_float_clamps_hi(self):
        """钳位上界：值 500 → 100。"""
        ds = _ds({"x": 500})
        assert ds.get_param_float("x", default=5.0, lo=1.0, hi=100.0) == 100.0

    def test_get_param_float_clamps_lo(self):
        """钳位下界：值 0 → 1。"""
        ds = _ds({"x": 0})
        assert ds.get_param_float("x", default=5.0, lo=1.0, hi=100.0) == 1.0

    def test_get_param_float_invalid_returns_default(self):
        """非法值（字符串）→ 回 default + 告警不崩。"""
        ds = _ds({"x": "not-a-number"})
        assert ds.get_param_float("x", default=5.0, lo=1.0, hi=100.0) == 5.0

    def test_get_param_float_in_range_untouched(self):
        """范围内原值通过（字符串数字可 float）。"""
        ds = _ds({"x": "7.5"})
        assert ds.get_param_float("x", default=5.0, lo=1.0, hi=100.0) == 7.5


# --- CircuitBreaker 从 ds 读参数 ---

class TestCircuitBreakerFromDs:

    def test_reads_params_from_ds(self):
        """ds 有 params.circuit_breaker → 阈值/超时取配置值。"""
        ds = _ds({"circuit_breaker": {"fail_threshold": 8, "reset_timeout": 120}})
        cb = CircuitBreaker(ds=ds)
        assert cb._fail_threshold == 8
        assert cb._reset_timeout == 120.0

    def test_defaults_when_ds_param_absent(self):
        """ds 无 circuit_breaker 配置 → 代码默认（5 次 / 60s）。"""
        cb = CircuitBreaker(ds=_ds({}))
        assert cb._fail_threshold == 5
        assert cb._reset_timeout == 60.0

    def test_explicit_args_beat_ds_params(self):
        """显式实参 > ds 配置（测试注入路径不被 params 覆盖）。"""
        ds = _ds({"circuit_breaker": {"fail_threshold": 8}})
        cb = CircuitBreaker(fail_threshold=2, ds=ds)
        assert cb._fail_threshold == 2

    def test_ds_without_get_param_skipped(self):
        """ds 无 get_param（外部替身/未继承基类）→ 跳过不崩，回默认。"""
        class _Bare:
            provider = "bare"
        cb = CircuitBreaker(ds=_Bare())
        assert cb._fail_threshold == 5
        assert cb._reset_timeout == 60.0

    def test_context_wires_ds_into_breaker(self):
        """rate_limit_context 把 ds 传给 CircuitBreaker：fail_threshold=1 → 单次失败即开。"""
        ds = _ds({"circuit_breaker": {"fail_threshold": 1}})
        with pytest.raises(ValueError):
            with rate_limit_context(ds, "ghost_api"):   # ghost=0 间隔不等待
                raise ValueError("boom")
        with pytest.raises(CircuitOpenError):
            with rate_limit_context(ds, "ghost_api"):
                pass


# --- Web 端点（GET presets / POST tier / POST override） ---

class _FakeConn:
    """最小 PG 替身：execute 首条返回预置行（fetchone），记录 UPDATE 供断言。"""

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
    """patch verify_jwt 伪造 admin payload（test_health_monitor 同款模式）。"""
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    from src.web_api import auth as _auth
    with patch.object(_auth, "verify_jwt",
                      return_value={"sub": "1", "username": "admin", "role": "admin"}):
        client = TestClient(app)
        client.headers.update({"Authorization": "Bearer test-token"})
        yield client


class _ConnPatch:
    """patch mgmt.get_conn 并保留假连接实例（供 UPDATE 断言）。"""

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


class TestPointsPresetsEndpoint:

    def test_get_returns_structure(self, admin_client):
        """GET presets：预设表（str 键）+ 当前档 + 逐 API 四值 + 熔断当前值。"""
        row = (5, json.dumps({"points_tier": 2000, "rate_limits": {"stk_mins": 0.25}}))
        with _ConnPatch(row):
            r = admin_client.get("/api/datasource/tushare/points-presets")
        assert r.status_code == 200
        body = r.json()
        assert set(body["presets"]) == {"200", "2000", "5000"}
        assert body["current_tier"] == 2000
        by_api = {a["api"]: a for a in body["apis"]}
        assert by_api["stk_mins"]["preset"] == 0.3       # 2000 档预设
        assert by_api["stk_mins"]["override"] == 0.25     # L2 覆写
        assert by_api["stk_mins"]["effective"] == 0.25    # 生效=覆写
        assert by_api["daily"]["preset"] == 0.2
        assert by_api["daily"]["override"] is None
        assert body["circuit_breaker"]["fail_threshold"] == 5     # 未配置回默认
        assert body["circuit_breaker"]["reset_timeout"] == 60.0

    def test_get_unknown_provider_404(self, admin_client):
        """未注册 provider → 404 DS_NOT_REGISTERED。"""
        r = admin_client.get("/api/datasource/no_such_provider/points-presets")
        assert r.status_code == 404
        assert r.json()["code"] == "DS_NOT_REGISTERED"

    def test_post_tier_invalid_rejected(self, admin_client):
        """档位 999 不在预设表 → 400 TIER_INVALID（写库前拦截）。"""
        r = admin_client.post("/api/datasource/tushare/points-tier", json={"tier": 999})
        assert r.status_code == 400
        assert r.json()["code"] == "TIER_INVALID"

    def test_post_tier_writes_and_returns_diff(self, admin_client):
        """切档 2000→5000：写 params.points_tier，diff 逐 API 给 before/after。"""
        row = (5, json.dumps({"points_tier": 2000}))
        with _ConnPatch(row) as conn:
            r = admin_client.post("/api/datasource/tushare/points-tier", json={"tier": 5000})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and body["tier"] == 5000
        diff = {d["api"]: d for d in body["diff"]}
        assert diff["daily"]["before"] == 0.2 and diff["daily"]["after"] == 0.1
        assert diff["stk_mins"]["before"] == 0.3 and diff["stk_mins"]["after"] == 0.12
        # 写库断言：UPDATE 恰一次，params JSON 含 points_tier=5000
        assert len(conn.updates) == 1
        saved = json.loads(conn.updates[0][1][0])
        assert saved["points_tier"] == 5000

    def test_post_override_null_deletes_override(self, admin_client):
        """value=null 删除覆写：rate_limits 清键，生效值回落预设。"""
        row = (5, json.dumps({"points_tier": 2000, "rate_limits": {"stk_mins": 0.25}}))
        with _ConnPatch(row) as conn:
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"api_name": "stk_mins", "value": None})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["effective"] == 0.3      # 回落 2000 档预设
        saved = json.loads(conn.updates[0][1][0])
        assert "stk_mins" not in saved["rate_limits"]   # 覆写键已删

    def test_post_override_value_out_of_range_rejected(self, admin_client):
        """覆写值 90000 > 86400 → 400 OVERRIDE_VALUE_INVALID。"""
        row = (5, json.dumps({}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"api_name": "daily", "value": 90000})
        assert r.status_code == 400
        assert r.json()["code"] == "OVERRIDE_VALUE_INVALID"

    def test_post_override_negative_rejected(self, admin_client):
        """覆写值 -1 < 0 → 400（间隔秒不可负）。"""
        row = (5, json.dumps({}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"api_name": "daily", "value": -1})
        assert r.status_code == 400
        assert r.json()["code"] == "OVERRIDE_VALUE_INVALID"

    def test_post_circuit_breaker_saved_merged(self, admin_client):
        """熔断参数写入：部分更新合并既有键 + 范围校验。"""
        row = (5, json.dumps({"circuit_breaker": {"fail_threshold": 8}}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"circuit_breaker": {"reset_timeout": 120}})
        assert r.status_code == 200
        cb = r.json()["circuit_breaker"]
        assert cb == {"fail_threshold": 8, "reset_timeout": 120.0}

    def test_post_circuit_breaker_invalid_rejected(self, admin_client):
        """fail_threshold 超范围（0）→ 400 CB_VALUE_INVALID。"""
        row = (5, json.dumps({}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override",
                                  json={"circuit_breaker": {"fail_threshold": 0}})
        assert r.status_code == 400
        assert r.json()["code"] == "CB_VALUE_INVALID"

    def test_post_no_body_fields_rejected(self, admin_client):
        """空 body（无 api_name 无 circuit_breaker）→ 400。"""
        row = (5, json.dumps({}))
        with _ConnPatch(row):
            r = admin_client.post("/api/datasource/tushare/rate-limit-override", json={})
        assert r.status_code == 400
        assert r.json()["code"] == "OVERRIDE_VALUE_INVALID"
