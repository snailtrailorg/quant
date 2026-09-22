"""Tushare Provider（批 63）——数据拉取（token 凭证）。"""
from .base import InterfaceProvider, register_provider


class TushareProvider(InterfaceProvider):
    provider = "tushare"
    market = "astock"

    FIELD_SCHEMA = [
        {"key": "token", "type": "text", "label_key": "interfaces.field.token", "secret": True},
    ]

    PARAMS_SCHEMA = []


register_provider(TushareProvider())
