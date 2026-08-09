"""LLM 网关 —— 平台所有 AI 调用的唯一入口。

用法:
    from src.llm_gateway import gateway
    resp = gateway.chat([{"role":"user","content":"你好"}], caller="test")
    print(resp.content)
"""

from .gateway import gateway, LLMGateway, LLMResponse, Tool, READ_TOOLS, OPERATIONAL_TOOLS

__all__ = ["gateway", "LLMGateway", "LLMResponse", "Tool", "READ_TOOLS", "OPERATIONAL_TOOLS"]