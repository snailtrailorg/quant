"""LLM 网关单测：token 限制 + 工具过滤（越权）+ 熔断半开。"""
import time
import pytest


# ── token 限制（输入程序控制）──

def test_estimate_tokens(gateway):
    """字符 ×1.5：'你好' 2 字 -> 3"""
    assert gateway._estimate_tokens([{"role": "user", "content": "你好"}]) == 3

def test_estimate_tokens_empty(gateway):
    assert gateway._estimate_tokens([]) == 0

def test_truncate_no_limit(gateway):
    """max_input=None 不截断"""
    msgs = [{"role": "user", "content": "你好"}]
    assert gateway._truncate_messages(msgs, None) == msgs

def test_truncate_under_limit(gateway):
    """不超限原样返回"""
    msgs = [{"role": "user", "content": "你好"}]
    assert gateway._truncate_messages(msgs, 100) == msgs

def test_truncate_over_limit_single(gateway):
    """单条超限截断内容到 max*2//3=66"""
    msgs = [{"role": "user", "content": "a" * 1000}]
    r = gateway._truncate_messages(msgs, 100)
    assert len(r[0]["content"]) == 66

def test_truncate_keep_system_latest(gateway):
    """超限保留 system + 最新，删中间历史"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old1" * 100},
        {"role": "assistant", "content": "old2" * 100},
        {"role": "user", "content": "new"},
    ]
    r = gateway._truncate_messages(msgs, 50)
    roles = [m["role"] for m in r]
    assert "system" in roles
    assert r[-1]["content"] == "new"

def test_check_input_chars_ok(gateway):
    gateway._check_input_chars([{"role": "user", "content": "x" * 100}])

def test_check_input_chars_reject(gateway):
    """超 50 万字符拒"""
    with pytest.raises(ValueError):
        gateway._check_input_chars([{"role": "user", "content": "x" * 500001}])


# ── 工具过滤（角色白名单 + 越权拒）──

def test_filter_viewer_read_only(gateway):
    tools = gateway._filter_tools("viewer", None)
    names = [t["function"]["name"] for t in tools]
    assert "query_position" in names
    assert "emergency_halt" not in names
    assert "risk_resume" not in names

def test_filter_viewer_cannot_override(gateway):
    """越权：viewer 传 emergency_halt 应被拒（交集空）"""
    from src.llm_gateway.gateway import Tool
    evil = [Tool(name="emergency_halt", description="", input_schema={})]
    assert gateway._filter_tools("viewer", evil) == []

def test_filter_trader_has_halt_no_resume(gateway):
    tools = gateway._filter_tools("trader", None)
    names = [t["function"]["name"] for t in tools]
    assert "emergency_halt" in names
    assert "strategy_start" in names
    assert "risk_resume" not in names

def test_filter_admin_has_resume(gateway):
    tools = gateway._filter_tools("admin", None)
    names = [t["function"]["name"] for t in tools]
    assert "risk_resume" in names

def test_filter_analyst_read_only(gateway):
    tools = gateway._filter_tools("analyst", None)
    names = [t["function"]["name"] for t in tools]
    assert "emergency_halt" not in names


# ── 熔断（closed/open/half_open）──

def test_circuit_closed(gateway):
    """未达 threshold 不熔断"""
    assert gateway._is_circuit_open("deepseek") is False

def test_circuit_open(gateway):
    """达 threshold(5) 熔断"""
    for _ in range(5):
        gateway._record_fail("deepseek")
    assert gateway._is_circuit_open("deepseek") is True

def test_circuit_half_open(gateway):
    """达 threshold + pause 过期 -> 半开放一个试探"""
    for _ in range(5):
        gateway._record_fail("deepseek")
    gateway._last_fail_time["deepseek"] = time.time() - 301  # > pause(300)
    assert gateway._is_circuit_open("deepseek") is False  # 放试探
    assert gateway._half_open["deepseek"] is True
    assert gateway._is_circuit_open("deepseek") is True  # 半开中其他跳过

def test_circuit_reset_on_success(gateway):
    """试探成功 -> closed"""
    for _ in range(5):
        gateway._record_fail("deepseek")
    gateway._reset_fail("deepseek")
    assert gateway._is_circuit_open("deepseek") is False
    assert gateway._failed_counts["deepseek"] == 0

def test_circuit_half_open_fail_back_to_open(gateway):
    """半开试探失败 -> 回 open"""
    for _ in range(5):
        gateway._record_fail("deepseek")
    gateway._last_fail_time["deepseek"] = time.time() - 301
    gateway._is_circuit_open("deepseek")  # 进半开
    gateway._record_fail("deepseek")  # 试探失败
    assert gateway._half_open["deepseek"] is False  # 回 open
