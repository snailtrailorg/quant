"""pytest 配置：gateway fixture mock DB/配置（不连真实 DB/文件）。"""
import pytest
from unittest.mock import patch

TEST_MODELS = [{
    "id": 1, "name": "test", "provider": "deepseek", "model": "deepseek-chat",
    "api_key": "fake-key", "base_url": "http://test.invalid", "context_window": 32768,
    "supports_tools": True, "max_input_tokens": 1000, "max_output_tokens": 500,
    "temperature": None, "priority": 1,
}]

TEST_FAILOVER = {"retry_wait_s": 2, "circuit_breaker": {"fail_threshold": 5, "pause_s": 300}}


@pytest.fixture
def gateway():
    """LLMGateway 实例，mock _load_models_from_db + _load_failover_config（不连 DB/文件）。"""
    from src.llm_gateway.gateway import LLMGateway
    with patch.object(LLMGateway, "_load_models_from_db", return_value=TEST_MODELS), \
         patch.object(LLMGateway, "_load_failover_config", return_value=TEST_FAILOVER):
        gw = LLMGateway()
        yield gw
