"""币安永续 Provider（批 63）——交易（api_key/api_secret 凭证）。"""
from .base import InterfaceProvider, register_provider


class BinancePerpProvider(InterfaceProvider):
    provider = "binance_perp"
    market = "crypto"

    FIELD_SCHEMA = [
        {"key": "api_key", "type": "text", "label_key": "interfaces.field.apiKey", "secret": False},
        {"key": "api_secret", "type": "text", "label_key": "interfaces.field.apiSecret", "secret": True},
    ]

    PARAMS_SCHEMA = []


register_provider(BinancePerpProvider())
