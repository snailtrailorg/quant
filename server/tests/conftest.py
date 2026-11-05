"""pytest 配置：gateway fixture mock DB/配置（不连真实 DB/文件）。"""
import os
# P4（2026-08-20）：crypto 密钥回退链收紧后（公开常量→进程随机），测试必须用固定测试密钥
# （否则随机钥解不开任何预加密 fixture；这也是改动正确暴露——本地 .env 此前一直无 JWT_SECRET）
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-prod")
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


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """限流/熔断注册表进程级（D2 跨轮记忆是运行期特性）——测试间清零，
    防熔断失败计数跨测试累积误开（engine 路径 now 走 rate_limit_context）。

    批 73：熔断状态迁 Valkey——一并 patch rate_limit._r 为每测试独立 FakeValkey
    （不连真 Valkey：跨测试键残留污染 + CI 无 Valkey 可达）；需要拿 fake 的测试
    （跨进程钉/TTL 自愈钉）请求本 fixture 得返回值。

    批 171a·D1 备注（步4 B-P1-2 裁定）：engine cap 冻结计数直调 business_redis，
    不在此全局 patch——card_ws dedup 闸/valkey 单源测试**刻意**用真 business_redis
    语义（全局 FakeValkey 误伤 14 用例，实测回滚）。freeze helper 自身 fail-soft
    （吞异常返 None）⇒ CI 无 Valkey 时测试仍绿；dev Valkey 可达时的 DEL 幂等无害
    （键不存在返 0、不建键）。测试面隔离由 test_batch171a_cursor_cap.py 自带 mock 承担。"""
    from unittest.mock import patch
    from src.data_platform import rate_limit
    from tests.fake_valkey import FakeValkey
    fake = FakeValkey()
    with patch.object(rate_limit, "_r", return_value=fake):
        rate_limit.reset_registries()
        yield fake
        rate_limit.reset_registries()
