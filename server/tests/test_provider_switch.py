"""provider 白名单 + 条件 SET + key 对齐测试（26 号收尾批 C 盲审 P2 补）。"""
import pytest

from src.web_api.errors import ApiError


def test_validate_provider_unknown():
    from src.web_api.routes.sync import _validate_provider
    with pytest.raises(ApiError) as e:
        _validate_provider("astock_daily", "unknown_provider")
    assert e.value.code == "SYNC_PROVIDER_INVALID"


def test_validate_provider_no_capability():
    """joinquant stub capabilities 空集 → astock_daily 不可切（盲审：白名单须 sid∈capabilities）。"""
    from src.web_api.routes.sync import _validate_provider
    with pytest.raises(ApiError) as e:
        _validate_provider("astock_daily", "joinquant")
    assert e.value.code == "SYNC_PROVIDER_NO_CAPABILITY"


def test_validate_provider_ok():
    from src.web_api.routes.sync import _validate_provider
    _validate_provider("astock_daily", "tushare")   # 不抛


def test_update_sync_config_partial_set():
    """条件 SET：旧调用不带 provider 不覆写（盲审 A-P1/B-P1 存量同族 bug）。"""
    from unittest.mock import patch, MagicMock
    from src.web_api.routes import sync as s
    conn = MagicMock()
    conn.__enter__.return_value = conn
    with patch.object(s, "get_conn", return_value=conn), \
         patch.object(s, "audit_log"):
        s.update_sync_config_api("astock_daily", {"enabled": True}, {"username": "test"})
    sql = conn.execute.call_args.args[0]
    assert "provider" not in sql                      # 不带 provider 不更新
    assert "enabled" in sql


def test_broker_registry_keys_aligned():
    from src.strategy_framework.broker import _REGISTRY
    assert set(_REGISTRY) == {"xtp", "binance_perp", "okx_perp"}
