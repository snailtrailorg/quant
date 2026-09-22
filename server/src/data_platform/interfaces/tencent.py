"""腾讯 Provider（批 63）——快照/分钟（免费接口，无凭证无参数）。"""
from .base import InterfaceProvider, register_provider


class TencentProvider(InterfaceProvider):
    provider = "tencent"
    market = "astock"

    FIELD_SCHEMA = []
    PARAMS_SCHEMA = []


register_provider(TencentProvider())
