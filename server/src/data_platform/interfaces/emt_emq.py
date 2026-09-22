"""东财 EMT/EMQ Provider（批 63）——东方财富证券机构交易平台。

EMT 极速柜台=交易（普通/信用/期权三套资金账号）+ EMQ 极速行情=行情（L1/L2）。
供应商按身份分组给字段（EMQ 行情身份 ≠ EMT 交易身份），schema 按原样分列不合并。
能力直标 NON_DATA_PROVIDERS（无 adapter，自研客户端 Phase 2）。
"""
from .base import InterfaceProvider, register_provider


class EmtEmqProvider(InterfaceProvider):
    provider = "emt_emq"
    market = "astock"

    FIELD_SCHEMA = [
        # EMQ 极速行情身份
        {"key": "emq_account", "type": "text", "label_key": "interfaces.field.emqAccount", "secret": False},
        {"key": "emq_password", "type": "text", "label_key": "interfaces.field.emqPassword", "secret": True},
        # EMT 极速柜台身份（普通/信用/期权三套资金账号，供应商原样分列）
        {"key": "emt_account", "type": "text", "label_key": "interfaces.field.emtAccount", "secret": False},
        {"key": "emt_password", "type": "text", "label_key": "interfaces.field.emtPassword", "secret": True},
        {"key": "emt_credit_account", "type": "text", "label_key": "interfaces.field.emtCreditAccount", "secret": False},
        {"key": "emt_credit_password", "type": "text", "label_key": "interfaces.field.emtCreditPassword", "secret": True},
        {"key": "emt_option_account", "type": "text", "label_key": "interfaces.field.emtOptionAccount", "secret": False},
        {"key": "emt_option_password", "type": "text", "label_key": "interfaces.field.emtOptionPassword", "secret": True},
    ]

    PARAMS_SCHEMA = [
        {"key": "emq_l1_host", "type": "text", "label_key": "interfaces.field.emqL1"},
        {"key": "emq_l2_host", "type": "text", "label_key": "interfaces.field.emqL2"},
        {"key": "emt_td_host", "type": "text", "label_key": "interfaces.field.emtTd"},
    ]


register_provider(EmtEmqProvider())
