"""D4 AI 日志归因单测（#34）。

mock gateway.chat，不连真实 LLM/DB。参照 test_factors 模式（直接调端点函数传 payload 绕 RBAC）。
"""
from unittest.mock import patch
from src.llm_gateway import gateway
from src.llm_gateway.gateway import LLMResponse


def test_log_analyze_with_logs():
    """传 logs 直接归因，过滤 INFO/DEBUG。"""
    from src.web_api.routes.auth_routes import log_analyze
    from src.web_api.models import LogAnalyzeReq
    req = LogAnalyzeReq(logs=[
        {"level": "ERROR", "module": "risk", "msg": "拒单：仓位超限"},
        {"level": "WARN", "module": "strategy", "msg": "信号被熔断"},
        {"level": "INFO", "module": "data", "msg": "日线完成"},  # 应被过滤
    ])
    with patch.object(gateway, "chat", return_value=LLMResponse(content="根因：风控拒单因仓位超限...")) as mock_chat:
        r = log_analyze(req, payload={"username": "admin", "role": "admin"})
    assert "根因" in r["analysis"]
    assert r["log_count"] == 2  # INFO 过滤掉
    assert mock_chat.call_args.kwargs.get("caller") == "log_analyze"


def test_log_analyze_no_logs():
    """无日志返回占位，不调 LLM。"""
    from src.web_api.routes.auth_routes import log_analyze
    from src.web_api.models import LogAnalyzeReq
    req = LogAnalyzeReq(logs=[])
    r = log_analyze(req, payload={"username": "admin", "role": "admin"})
    assert r["log_count"] == 0
    assert r["analysis"]  # 非空占位
