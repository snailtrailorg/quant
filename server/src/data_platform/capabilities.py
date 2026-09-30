"""批55-0:供应商能力查询与 ⊆ 校验(数据层——可向下 import quant_common 词表)。

能力真源=代码(adapter.capabilities sync_id 串,经 SYNC_ID_CAP_MAP 归一为能力集 token);
配置列恒为「用户启用子集」——55a 端点写侧校验+启动/GET 漂移告警共用本函数。

批 83a 拆表:另加**域维**(域=表,markets.DOMAIN_*)——写侧除「⊆代码能力」外还须
「⊆本域能力集」(合表时代可建出跨域揉合行,拆表后不可能)。
"""
from __future__ import annotations

from src.quant_common.markets import NON_DATA_PROVIDERS, SYNC_ID_CAP_MAP


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


# ——— 批 83a:域维(域=表) ———

def domain_capabilities(domain: str) -> set[str]:
    """域能力集(domain=markets.DOMAIN_DATA/DOMAIN_TRADING)。未知名=空集(上层另判)。"""
    from src.quant_common.markets import DOMAIN_CAPS
    return set(DOMAIN_CAPS.get(domain, ()))


def check_capability_domain(domain: str, declared: set[str]) -> tuple[bool, str]:
    """配置声明能力 ⊆ 本域能力集?(83a 收窄立法——越域即揉合行,拆表后禁建)。"""
    allowed = domain_capabilities(domain)
    excess = set(declared) - allowed
    if excess:
        return False, (f"{sorted(excess)} 不属于「{domain}」域能力集 {sorted(allowed)}"
                       f"——拆表后每行单域,请改用对应端点族")
    return True, ""


def provider_domain(provider: str) -> str | None:
    """provider 归属域(83a):按代码能力集 ⊆ 单一域能力集推导。

    返回 markets.DOMAIN_DATA/DOMAIN_TRADING;空能力集(stub 源)或跨域(未来多角色 provider)
    =None——此类 provider 不出现在任一域目录(下拉防误导),写入由 check_capability_domain 兜底。
    """
    from src.quant_common.markets import DOMAIN_CAPS
    caps = provider_capabilities(provider)
    if not caps:
        return None
    hits = [d for d, dc in DOMAIN_CAPS.items() if caps <= set(dc)]
    return hits[0] if len(hits) == 1 else None
