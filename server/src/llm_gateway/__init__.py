"""LLM 网关 —— 平台所有 AI 调用的唯一入口。

用法:
    from src.llm_gateway import gateway
    resp = gateway.chat([{"role":"user","content":"你好"}], caller="test")
    print(resp.content)
"""

from .gateway import (
    ADMIN_TOOLS, OPERATIONAL_TOOLS, READ_TOOLS, TRADER_TOOLS, UNFREEZE_TOOLS,
    LLMGateway, LLMResponse, Tool, gateway,
)

__all__ = [
    "gateway", "LLMGateway", "LLMResponse", "Tool", "READ_TOOLS",
    "OPERATIONAL_TOOLS", "TRADER_TOOLS", "ADMIN_TOOLS", "UNFREEZE_TOOLS",
]
