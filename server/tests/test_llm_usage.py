"""LLM 用量 API 单测（真实本地 DB，验证聚合结构 + 字段）。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_llm_usage_summary_structure():
    from src.web_api.routes.chat import llm_usage_summary
    result = llm_usage_summary(payload={"username": "test", "role": "admin"})
    assert "today" in result
    assert "month" in result
    assert "trend" in result
    assert isinstance(result["today"], list)
    assert isinstance(result["month"], list)
    assert isinstance(result["trend"], list)


def test_llm_usage_summary_fields():
    from src.web_api.routes.chat import llm_usage_summary
    result = llm_usage_summary(payload={"username": "test", "role": "admin"})
    # 若有数据，验证字段
    for row in result["month"]:
        assert "provider" in row and "model" in row and "calls" in row
        assert "input_tokens" in row and "output_tokens" in row
        assert "avg_latency_ms" in row and "success_rate" in row
    for t in result["trend"]:
        assert "date" in t and "calls" in t and "total_tokens" in t and "avg_latency_ms" in t
