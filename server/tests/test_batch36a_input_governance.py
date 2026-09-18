"""批36a · 全站输入合法性治理（资金面+P0）单测。

覆盖（docs/任务/批36-全站输入合法性治理.md v2）：
- risk_schema.validate_params：type 白名单/JSON/dict/逐键 (type,key) 查表（含 crypto
  daily_loss_limit 比例 vs registry max_loss 绝对额的复合键钉）/int 整数性
- RuleSanitizer 三层：非 dict 整组回落/键类型非法回落缺省/数值超界钳位+开区间回落/
  max_trades_per_day=0 不限制保留/去抖（同指纹一次）
- 写端点：RISK_PARAM_INVALID 400（routes/risk.py 双端点）
- manual-order 负量 400 / exempt 负值+过期日期 400
- 回测写端点：负佣金 400（造假面）/capital 越界/params 覆盖保留键后终值判定
- validate_parameter_defs：number 型 min/max 必填+min<max+default∈[min,max]（用户裁定立法）
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


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


# ——— validate_params（写侧查表）———

def test_validate_ok_and_compound_key():
    """合法过 + 复合键：crypto.daily_loss_limit=比例(0,1] 合法、registry.daily_loss_limit.max_loss=绝对额 5 万合法。"""
    from src.risk_control.risk_schema import validate_params
    out, err = validate_params("global", '{"max_drawdown": 0.2, "daily_loss_limit": 0.05}')
    assert err == "" and out["max_drawdown"] == 0.2
    out2, err2 = validate_params("daily_loss_limit", '{"max_loss": 50000}')
    assert err2 == "" and out2["max_loss"] == 50000   # 绝对额平铺键表会误拒——复合键钉


def test_validate_rejects():
    from src.risk_control.risk_schema import validate_params
    assert validate_params("ghost", "{}")[1].startswith("未知规则类型")
    assert "JSON 对象" in validate_params("global", "[1]")[1]
    assert validate_params("global", "not json")[1]
    assert "须为数字" in validate_params("global", '{"max_drawdown": "0.2"}')[1]   # 崩溃源路径（A-P0-1）
    assert "不得大于" in validate_params("global", '{"max_drawdown": 5}')[1]
    assert "未知参数" in validate_params("global", '{"evil": 1}')[1]
    assert "须为整数" in validate_params("etf_conv", '{"max_trades_per_day": 2.5}')[1]


# ——— RuleSanitizer（消费侧三层）———

def test_sanitizer_three_layers():
    import logging
    from src.risk_control.risk_schema import RuleSanitizer
    s = RuleSanitizer(logging.getLogger("t36"))
    assert s.sanitize_type("global", [1]) == {}                       # ①非 dict 整组回落
    assert s.sanitize_type("global", {"max_drawdown": "0.2"}) == {}  # ②键类型非法→键剔除（合并=base 缺省）
    assert s.sanitize_type("global", {"max_drawdown": 5}) == {"max_drawdown": 1}   # ③超界钳 hi
    assert s.sanitize_type("global", {"max_drawdown": 0}) == {}      # ③开区间下界无法钳→回落（不入 out）
    assert s.sanitize_type("etf_conv", {"max_trades_per_day": 0}) == {"max_trades_per_day": 0}   # 0=不限制保留


def test_sanitizer_warn_once_dedup():
    import logging
    from src.risk_control.risk_schema import RuleSanitizer
    s = RuleSanitizer(logging.getLogger("t36b"))
    s.sanitize_type("global", {"max_drawdown": 5})
    n1 = len(s._warned)
    s.sanitize_type("global", {"max_drawdown": 5})   # 同指纹二跑不增告警（B-P1-2 防刷屏）
    assert len(s._warned) == n1


def test_load_rules_sanitizes_and_no_crash():
    """__init__ 崩溃路径闭环钉：DB 返回坏形状/字符串值不再 TypeError（原 {**base,**[1]} 崩）。"""
    from src.risk_control.risk import RiskControl
    rows = [("global", '[1]'),                    # 非 dict → 整组回落
            ("etf_conv", '{"max_trades_per_day": "many"}')]   # 字符串值 → 键回落
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = rows
    with patch("src.data_platform.db.get_conn", return_value=conn):
        rc = RiskControl()   # 原实现此处 TypeError
    assert rc._rules["global"]["max_drawdown"] == 0.15   # 回落 DEFAULT
    assert rc._rules["etf_conv"]["max_trades_per_day"] == 20


# ——— 写端点 ———

def test_risk_rule_create_rejects_bad_params(authed_client):
    conn = MagicMock(); conn.__enter__.return_value = conn
    with patch("src.web_api.routes.risk.get_conn", return_value=conn), \
         patch("src.web_api.routes.risk.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        r = authed_client.post("/api/risk-rules",
                               json={"name": "x", "type": "ghost", "params": "{}", "enabled": True})
    assert r.status_code == 400 and r.json().get("code") == "RISK_PARAM_INVALID"


def test_risk_rule_types_shape(authed_client):
    """types 端点 schema 单源下发形状（active 标注+params 元数据）。"""
    with patch("src.web_api.routes.risk.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        r = authed_client.get("/api/risk-rules/types")
    assert r.status_code == 200
    tys = {t["type"]: t for t in r.json()["types"]}
    assert set(tys) == {"global", "etf_conv", "crypto", "max_position", "max_single_order", "daily_loss_limit"}
    assert tys["global"]["active"] is True and tys["max_position"]["active"] is False
    assert any(p["key"] == "max_drawdown" for p in tys["global"]["params"])
    # 批45：pct 键 percent=True 透传（前端显示层 ×100 百分比化驱动）；金额键无
    _g = {p["key"]: p for p in tys["global"]["params"]}
    assert _g["max_drawdown"]["percent"] is True and _g["daily_loss_limit"]["percent"] is True
    _e = {p["key"]: p for p in tys["etf_conv"]["params"]}
    assert _e["single_position_pct"]["percent"] is True and "percent" not in _e["max_single_amount"]
    _c = {p["key"]: p for p in tys["crypto"]["params"]}
    assert _c["daily_loss_limit"]["percent"] is True and "percent" not in _c["leverage_max"]
    assert not any(p.get("percent") for p in tys["daily_loss_limit"]["params"])   # 绝对额类零 percent


# ——— 批45：GET bounds percent 下发（显示层百分比化元数据随值域同源下发） ———

def test_system_config_bounds_percent_downstream(authed_client):
    """GET config：五 pct 键 bounds.percent=true、int 键 false、text 键 bounds=null。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [
        ("alert_disk_warn", "0.8", "float", "d", None, None),
        ("celery_concurrency", "2", "int", "d", None, None),
        ("base_url", "", "text", "d", None, None),
    ]
    with patch("src.web_api.routes.system.get_conn", return_value=conn), \
         patch("src.web_api.routes.system.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        r = authed_client.get("/api/system-config")
    assert r.status_code == 200
    items = {i["key"]: i for i in r.json()["items"]}
    assert items["alert_disk_warn"]["bounds"] == {"lo": 0, "hi": 1, "lo_open": True, "percent": True}
    assert items["celery_concurrency"]["bounds"]["percent"] is False
    assert items["base_url"]["bounds"] is None   # text 键无 bounds（前端可选链防 TypeError——盲审 A-P1-2 钉）


# ——— manual-order / exempt ———

def test_manual_order_rejects_nonpositive(authed_client):
    conn = MagicMock(); conn.__enter__.return_value = conn
    with patch("src.web_api.routes.risk.get_conn", return_value=conn), \
         patch("src.web_api.routes.risk.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        r = authed_client.post("/api/reconcile/manual-order", json={"symbol": "600000", "volume": -100})
    assert r.status_code == 400


def test_exempt_rejects_negative_and_past_date(authed_client):
    from datetime import date, timedelta
    conn = MagicMock(); conn.__enter__.return_value = conn
    with patch("src.web_api.routes.risk.get_conn", return_value=conn), \
         patch("src.web_api.routes.risk.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        r1 = authed_client.post("/api/reconcile/issues/1/exempt",
                                json={"exempt_qty": -5, "exempt_until": str(date.today())})
        past = str(date.today() - timedelta(days=1))
        r2 = authed_client.post("/api/reconcile/issues/1/exempt",
                                json={"exempt_qty": 100, "exempt_until": past})
    assert r1.status_code == 400 and r2.status_code == 400


# ——— 回测写端点 ———

def _bt_conn(strategy_params='{"parameter_defs": []}'):
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.side_effect = [
        (strategy_params,),   # strategy_config 查询
        (101,),               # INSERT RETURNING
    ]
    return conn


def test_backtest_rejects_negative_commission(authed_client):
    """负佣金=回测造假面（A-P1-6 断言修正后的真口子）——400。"""
    from src.scheduler import tasks as _t
    with patch("src.web_api.routes.backtest.get_conn", return_value=_bt_conn()), \
         patch("src.web_api.routes.backtest.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch.object(_t.backtest_run_task, "delay"):
        r = authed_client.post("/api/backtest",
                               json={"strategy_config_id": 1, "symbols": ["600000.SHSE"],
                                     "params": {"commission": -0.001}})
    assert r.status_code == 400 and "佣金" in r.json()["detail"]


def test_backtest_params_override_reserved_keys_checked(authed_client):
    """保留键被 params 覆盖后按终值判定（前端 :286 展开顺序同病——B-P2-6）。"""
    from src.scheduler import tasks as _t
    with patch("src.web_api.routes.backtest.get_conn", return_value=_bt_conn()), \
         patch("src.web_api.routes.backtest.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch.object(_t.backtest_run_task, "delay"):
        r = authed_client.post("/api/backtest",
                               json={"strategy_config_id": 1, "symbols": ["600000.SHSE"],
                                     "params": {"capital": 5}})
    assert r.status_code == 400


# ——— validate_parameter_defs 立法 ———

def test_defs_legislation():
    from src.strategy_framework.strategy import validate_parameter_defs as v
    assert v([{"name": "x", "type": "number", "default": 1, "min": 0, "max": 10}]) is None
    assert "min/max 必填" in v([{"name": "x", "type": "number", "default": 1}])           # 用户裁定：必填
    assert "小于最大值" in v([{"name": "x", "type": "number", "default": 1, "min": 10, "max": 0}])
    assert "不在" in v([{"name": "x", "type": "number", "default": 99, "min": 0, "max": 10}])
    assert v([{"name": "b", "type": "boolean", "default": True}]) is None                  # 非 number 零影响


def test_backtest_symbol_params_bypass_closed(authed_client):
    """盲审 A-P1-2 两方向钉：①run 级 params 越界+symbol_params 合法覆盖——原实现 merged 判定可绕，
    现分层校验（引擎只读 run 级）须 400；②symbol_params 内含保留键（引擎不消费=脏配置）400。"""
    from src.scheduler import tasks as _t
    with patch("src.web_api.routes.backtest.get_conn", return_value=_bt_conn()), \
         patch("src.web_api.routes.backtest.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch.object(_t.backtest_run_task, "delay"):
        r1 = authed_client.post("/api/backtest",
                                json={"strategy_config_id": 1, "symbols": ["600000.SHSE"],
                                      "params": {"capital": 5e11},
                                      "symbol_params": {"600000.SHSE": {"capital": 1000000}}})
        assert r1.status_code == 400   # ①run 级 5e11 不因 per-symbol 合法覆盖而放行
        r2 = authed_client.post("/api/backtest",
                                json={"strategy_config_id": 1, "symbols": ["600000.SHSE"],
                                      "params": {"capital": 1000000},
                                      "symbol_params": {"600000.SHSE": {"commission": 0.001}}})
        assert r2.status_code == 400 and "不可包含" in r2.json()["detail"]   # ②保留键禁入 symbol_params
        r3 = authed_client.post("/api/backtest",
                                json={"strategy_config_id": 1, "symbols": ["600000.SHSE"],
                                      "params": {"capital": 1000000},
                                      "symbol_params": {"600000.SHSE": {"my_param": 7}}})
        assert r3.status_code == 200   # 正常路径：策略参数覆盖不受影响


def test_validate_params_nan_and_optional_null():
    """盲审 A-P1-3/B-P2-3：NaN 四比较全 False 穿透→须拒；optional 键 null=不配置（清空合法）。"""
    from src.risk_control.risk_schema import validate_params, RuleSanitizer
    import logging
    _, err = validate_params("global", '{"max_drawdown": NaN}')
    assert err and "有限" in err
    assert validate_params("etf_conv", '{"strict_stop_loss": null}')[0] == {}   # optional null 跳过
    s = RuleSanitizer(logging.getLogger("t36c"))
    assert s.sanitize_type("global", {"max_drawdown": float("nan")}) == {}   # NaN 消费侧同样回落


# ——— 批36b-α：system_config 注册表/交叉/collector 钳位/models Field ———

def test_system_config_bounds_registry(authed_client):
    """注册表查表：阈值 >1 拒/concurrency 0 拒/xtp 负值（=禁用语义）放行/quota 域。"""
    from src.web_api.routes.system import SYSTEM_CONFIG_BOUNDS
    assert SYSTEM_CONFIG_BOUNDS["alert_mem_crit"] == (0, 1, True, True)   # 批45 四元（第四元 percent）
    assert SYSTEM_CONFIG_BOUNDS["xtp_session_lead_min"][0] < 0   # ≤0=禁用铁律不锁死（B-P1-4）
    assert SYSTEM_CONFIG_BOUNDS["user_bot_quota"] == (1, 100, False, False)
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.side_effect = [
        ("float",), (None,),   # value_type 查询 / 交叉校验查 crit（无行）
    ]
    with patch("src.web_api.routes.system.get_conn", return_value=conn), \
         patch("src.web_api.routes.system.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.routes.system.audit_log"):
        r = authed_client.post("/api/system-config/alert_mem_warn", json={"value": 1.5})
    assert r.status_code == 400 and "不得大于" in r.json()["detail"]


def test_system_config_cross_validation(authed_client):
    """阈值对交叉：warn ≥ 现 crit 值拒（预警先于严重）。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.side_effect = [("float",), ("0.9",)]
    with patch("src.web_api.routes.system.get_conn", return_value=conn), \
         patch("src.web_api.routes.system.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.routes.system.audit_log"):
        r = authed_client.post("/api/system-config/alert_mem_warn", json={"value": 0.95})
    assert r.status_code == 400 and "须小于" in r.json()["detail"]


def test_collector_clamps_thresholds():
    """消费侧钳位：DB 阈值 >1 钳 1.0 / NaN 回落缺省（监控告警静默失效面）。"""
    import importlib
    from src.health_monitor import collector as C
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [
        ("alert_mem_warn", "1.7"), ("alert_mem_crit", "nan"), ("collect_period_mem", "-5")]
    with patch("src.data_platform.db.get_conn", return_value=conn):
        C._CFG_CACHE.update(thresholds=None, ts=0)
        th, periods = C._read_cfg()
    assert th["mem"]["warn"] == 1.0            # 钳 hi
    assert th["mem"]["crit"] == C._DEF_THRESHOLDS["mem"]["crit"]   # NaN 回落缺省
    assert periods["mem"] == C._DEF_PERIODS["mem"]                 # 负周期回落缺省


def test_models_field_constraints():
    """模型层 Field：负 token 限额/阈值 0/杠杆 0——422（pydantic 校验错）。
    批50：LlmBudgetReq 断言随预算链退役删除（负限额用例改挂 SmtpCard 域外——llm 域无 Field 模型剩余）。"""
    from src.web_api.models import StrategyAccountReq
    import pytest as _pytest
    from pydantic import ValidationError
    with _pytest.raises(ValidationError):
        StrategyAccountReq(strategy_id="s", account_id="a", initial_capital=0)


def test_sms_config_three_semantics(authed_client):
    """批38 用户裁定：sms-config 三段语义——缺键=不改/密钥空=跳过/明文空=真清空。"""
    from src.web_api.routes import alerts as A
    conn = MagicMock(); conn.__enter__.return_value = conn
    calls = []
    conn.execute.side_effect = lambda sql, *a: calls.append((sql, a)) or None
    with patch("src.web_api.routes.alerts.get_conn", return_value=conn), \
         patch("src.web_api.routes.alerts.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.routes.alerts.audit_log"):
        # 密钥空=跳过 + 明文空=清空落库（空串仍 execute）+ 缺键（access_key_id 不发）=跳过
        authed_client.put("/api/alerts/sms-config",
                          json={"access_key_secret": "", "sign_name": "", "template_code": "SMS_1"})
    writes = [c for c in calls if "system_config" in c[0] and "INSERT" in c[0]]
    keys = [w[0] for w in writes]
    assert any("alert_sms_sign_name" in k for k in keys)          # 明文空=清空（落库空串）
    assert any("alert_sms_template_code" in k for k in keys)      # 明文非空=正常
    assert not any("alert_sms_access_key_secret" in k for k in keys)   # 密钥空=跳过
    assert not any("alert_sms_access_key_id" in k for k in keys)       # 缺键=跳过
