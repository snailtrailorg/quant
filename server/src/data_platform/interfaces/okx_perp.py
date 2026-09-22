"""OKX 永续 Provider（批 63）——交易（api_key/api_secret/passphrase 凭证）。"""
from .base import InterfaceProvider, register_provider


class OkxPerpProvider(InterfaceProvider):
    provider = "okx_perp"
    market = "crypto"

    FIELD_SCHEMA = [
        {"key": "api_key", "type": "text", "label_key": "interfaces.field.apiKey", "secret": False},
        {"key": "api_secret", "type": "text", "label_key": "interfaces.field.apiSecret", "secret": True},
        {"key": "passphrase", "type": "text", "label_key": "interfaces.field.passphrase", "secret": True},
    ]

    PARAMS_SCHEMA = []


register_provider(OkxPerpProvider())
