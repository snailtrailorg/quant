"""中泰 XTP Provider（批 63）——交易+行情多能力接口（vnpy_xtp 网关）。

凭证 app_id/app_secret 共用一份身份（MD/TD）；地址/端口/客户号在 params（非秘密）。
"""
from .base import InterfaceProvider, register_provider


class XtpProvider(InterfaceProvider):
    provider = "xtp"
    market = "astock"

    FIELD_SCHEMA = [
        {"key": "app_id", "type": "text", "label_key": "interfaces.field.appId", "secret": False},
        {"key": "app_secret", "type": "text", "label_key": "interfaces.field.appSecret", "secret": True},
        {"key": "auth_code", "type": "text", "label_key": "interfaces.field.authCode", "secret": False},
    ]

    PARAMS_SCHEMA = [
        {"key": "md_host", "type": "text", "label_key": "interfaces.field.mdHost"},
        {"key": "md_port", "type": "number", "label_key": "interfaces.field.mdPort"},
        {"key": "td_host", "type": "text", "label_key": "interfaces.field.tdHost"},
        {"key": "td_port", "type": "number", "label_key": "interfaces.field.tdPort"},
        {"key": "client_id", "type": "number", "label_key": "interfaces.field.clientId"},
    ]


register_provider(XtpProvider())
