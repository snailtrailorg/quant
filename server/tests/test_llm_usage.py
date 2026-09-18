"""LLM 用量 series API 单测（真实本地 DB，验证卡片数据源形状——批50 重写）。
原 summary 端点（today/month/trend）随批退役。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_llm_usage_series_structure():
    from src.web_api.routes.chat import llm_usage_series
    result = llm_usage_series(payload={"username": "test", "role": "admin"})
    assert "models" in result and isinstance(result["models"], list)
    for m in result["models"]:
        assert "provider" in m and "model" in m
        assert "today" in m and {"calls", "tokens", "success_rate"} <= set(m["today"])
        # 48h×小时粒度补零曲线（generate_series——前端零补逻辑）
        assert isinstance(m["series"], list) and len(m["series"]) == 48
        assert all({"ts", "calls", "tokens"} == set(p) for p in m["series"])
