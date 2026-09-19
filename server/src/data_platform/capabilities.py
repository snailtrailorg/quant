"""批55-0:供应商能力查询与 ⊆ 校验(数据层——可向下 import quant_common 词表)。

能力真源=代码(adapter.capabilities sync_id 串,经 CAPABILITY_MAP 归一为配置层枚举);
配置列恒为「用户启用子集」——55a 端点写侧校验+启动/GET 漂移告警共用本函数。
"""
from __future__ import annotations
from src.quant_common.markets import SYNC_ID_CAP_MAP, NON_DATA_PROVIDERS


def provider_capabilities(provider: str) -> set[str]:
    """供应商的代码层能力集(sync_id 归一+无 adapter 通道直标)。"""
    from src.data_platform.adapters.base import _ADAPTERS
    cls = _ADAPTERS.get(provider)
    if cls is not None:
        return {SYNC_ID_CAP_MAP.get(c, c) for c in getattr(cls, "capabilities", set())}
    return set(NON_DATA_PROVIDERS.get(provider, set()))


def check_capability_subset(provider: str, declared: set[str]) -> tuple[bool, str]:
    """配置声明能力 ⊆ 代码能力?(ok, 越集说明)。"""
    code_caps = provider_capabilities(provider)
    excess = set(declared) - code_caps
    if excess:
        return False, f"{provider} 代码能力集 {sorted(code_caps)} 不含 {sorted(excess)}——配置能力须为启用子集"
    return True, ""
