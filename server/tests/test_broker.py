"""交易通道单测：Broker 实现（get_credentials 解密 + test_connection 检查字段）。"""
import json
from unittest.mock import patch


def test_xtp_broker_test_ok():
    from src.strategy_framework.broker import XTPBroker
    cred = json.dumps({"app_id": "x", "app_secret": "y"})
    with patch("src.quant_common.crypto.decrypt", return_value=cred):
        b = XTPBroker(credentials_encrypted="enc")
        assert b.test_connection() is True


def test_xtp_broker_missing_field():
    from src.strategy_framework.broker import XTPBroker
    cred = json.dumps({"app_id": "x"})  # 缺 app_secret
    with patch("src.quant_common.crypto.decrypt", return_value=cred):
        b = XTPBroker(credentials_encrypted="enc")
        assert b.test_connection() is False


def test_binance_broker_required():
    from src.strategy_framework.broker import BinanceBroker
    cred = json.dumps({"api_key": "k", "api_secret": "s"})
    with patch("src.quant_common.crypto.decrypt", return_value=cred):
        b = BinanceBroker(credentials_encrypted="enc")
        assert b.test_connection() is True


def test_okx_broker_needs_passphrase():
    from src.strategy_framework.broker import OKXBroker
    cred = json.dumps({"api_key": "k", "api_secret": "s"})  # 缺 passphrase
    with patch("src.quant_common.crypto.decrypt", return_value=cred):
        b = OKXBroker(credentials_encrypted="enc")
        assert b.test_connection() is False


def test_get_broker_unknown_provider():
    from src.strategy_framework.broker import get_broker
    assert get_broker("unknown_broker_xyz") is None


def test_broker_no_credentials():
    """无凭证返回空 dict + test 失败"""
    from src.strategy_framework.broker import XTPBroker
    b = XTPBroker(credentials_encrypted=None)
    assert b.get_credentials() == {}
    assert b.test_connection() is False


# ── ST7 双轨会话身份（2026-08-25）：client_id 覆写 + 通道级参数 ──

def test_build_xtp_setting_client_id_override():
    """client_id 覆写 .env 路径客户号（同账号同号互斥的解法，XTP 官方规则）。"""
    import os
    from unittest.mock import patch
    from src.strategy_framework.broker import build_xtp_setting
    env = {"XTP_TEST_ACCOUNT": "acc", "XTP_TEST_CLIENT_ID": "1"}
    with patch.dict(os.environ, env, clear=False):
        base = build_xtp_setting()
        assert base["客户号"] == 1
        over = build_xtp_setting(client_id=2)
        assert over["客户号"] == 2


def test_build_xtp_setting_client_id_override_db_path():
    """双盲审 P2：client_id 覆写 Broker DB 路客户号——DB 记录/params 默认 1 号
    也须被 runner 独立号压过（覆写单机制在 build 内两条路径都要生效）。"""
    from types import SimpleNamespace
    from unittest.mock import patch
    from src.strategy_framework.broker import build_xtp_setting
    fake_broker = SimpleNamespace(
        get_credentials=lambda: {"app_id": "acc", "app_secret": "sec", "client_id": "1"},
        _params={"md_host": "h", "md_port": 1, "td_host": "t", "td_port": 2, "client_id": 1},
    )
    with patch("src.strategy_framework.broker.get_broker", return_value=fake_broker):
        assert build_xtp_setting(client_id=2)["客户号"] == 2
        assert build_xtp_setting()["客户号"] == 1   # 不传参时 DB 默认原样


def test_get_xtp_param_fallback_default():
    """broker_config 无 xtp 记录/异常：回 default（runner 未配号时行为不变）。"""
    from src.strategy_framework.broker import get_xtp_param
    assert get_xtp_param("client_id_runner") is None
    assert get_xtp_param("client_id_runner", 2) == 2
