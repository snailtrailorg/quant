"""交易通道单测：Broker 实现（get_credentials 解密 + test_connection 检查字段）。"""
import json
from unittest.mock import patch


def test_xtp_broker_test_ok():
    from src.strategy_framework.broker import XTPBroker
    cred = json.dumps({"app_id": "x", "app_secret": "y"})
    with patch("src.web_api.crypto_utils.decrypt", return_value=cred):
        b = XTPBroker(credentials_encrypted="enc")
        assert b.test_connection() is True


def test_xtp_broker_missing_field():
    from src.strategy_framework.broker import XTPBroker
    cred = json.dumps({"app_id": "x"})  # 缺 app_secret
    with patch("src.web_api.crypto_utils.decrypt", return_value=cred):
        b = XTPBroker(credentials_encrypted="enc")
        assert b.test_connection() is False


def test_binance_broker_required():
    from src.strategy_framework.broker import BinanceBroker
    cred = json.dumps({"api_key": "k", "api_secret": "s"})
    with patch("src.web_api.crypto_utils.decrypt", return_value=cred):
        b = BinanceBroker(credentials_encrypted="enc")
        assert b.test_connection() is True


def test_okx_broker_needs_passphrase():
    from src.strategy_framework.broker import OKXBroker
    cred = json.dumps({"api_key": "k", "api_secret": "s"})  # 缺 passphrase
    with patch("src.web_api.crypto_utils.decrypt", return_value=cred):
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
