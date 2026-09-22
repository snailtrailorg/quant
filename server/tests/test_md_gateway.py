"""批 63（二）行情网关插件 + 接口行选行测试钉（双盲审 P0-1/P1-1/P1-3/P2-1 回归钉）。"""
import json
from unittest.mock import MagicMock, patch

import pytest


def _conn(rows):
    """get_conn mock：with 语法 + execute 返回 fetchone。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchone.return_value = rows[0] if rows else None
    conn.execute.return_value = cur
    return conn


# ——— 网关注册表 / create_md_gateway ———

def test_list_md_gateway_providers_has_xtp():
    from src.strategy_framework.md_gateway import list_md_gateway_providers
    assert "xtp" in list_md_gateway_providers()


def test_create_md_gateway_unknown_provider_raises():
    from src.strategy_framework.md_gateway import create_md_gateway
    with pytest.raises(ValueError):
        create_md_gateway("no_such_provider", MagicMock())


# ——— xtp_setting_from 分支 ———

def test_xtp_setting_from_cred_path():
    from src.strategy_framework.broker import xtp_setting_from
    s = xtp_setting_from({"app_id": "A", "app_secret": "S", "auth_code": "K"},
                         {"md_host": "h", "md_port": 6001, "client_id": 7})
    assert s["账号"] == "A" and s["密码"] == "S" and s["客户号"] == 7
    assert s["行情地址"] == "h" and s["行情端口"] == 6001


def test_xtp_setting_from_env_fallback(monkeypatch):
    from src.strategy_framework.broker import xtp_setting_from
    monkeypatch.setenv("XTP_TEST_ACCOUNT", "envA")
    monkeypatch.setenv("XTP_TEST_QUOTE_HOST", "envh")
    s = xtp_setting_from({}, {})
    assert s["账号"] == "envA" and s["行情地址"] == "envh"


# ——— get_interface_row：P0-1/P2-1 回归钉 ———

def _xtp_row(cred_enc="ENC", params=None):
    return ("xtp", cred_enc, params or {}, "astock", ["trading", "quote"])


def test_get_interface_row_missing_required_field_raises():
    """P0-1 回归：指定行凭证非空但缺 app_id（必填非密字段）→ raise，禁 .env fallback。"""
    from src.strategy_framework.broker import get_interface_row
    with patch("src.data_platform.db.get_conn",
               return_value=_conn([_xtp_row()])), \
         patch("src.quant_common.crypto.decrypt",
               return_value=json.dumps({"app_secret": "S"})):   # 缺 app_id
        with pytest.raises(RuntimeError):
            get_interface_row(row_id=1)


def test_get_interface_row_decrypt_failure_raises():
    """P2-1 回归：解密失败 raise（密钥错配 fail-fast），不吞成无凭证静默 .env。"""
    from src.strategy_framework.broker import get_interface_row
    with patch("src.data_platform.db.get_conn",
               return_value=_conn([_xtp_row()])), \
         patch("src.quant_common.crypto.decrypt", side_effect=Exception("bad key")):
        with pytest.raises(RuntimeError):
            get_interface_row(row_id=1)


def test_get_interface_row_complete_credentials_ok():
    """凭证完整（app_id+app_secret）→ 正常返回。"""
    from src.strategy_framework.broker import get_interface_row
    with patch("src.data_platform.db.get_conn",
               return_value=_conn([_xtp_row()])), \
         patch("src.quant_common.crypto.decrypt",
               return_value=json.dumps({"app_id": "A", "app_secret": "S"})):
        row = get_interface_row(row_id=1)
    assert row["provider"] == "xtp" and row["credentials"]["app_id"] == "A"
