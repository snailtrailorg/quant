"""D3 可转债条款解读单测（#33）。

mock gateway.chat + pull_cb_basic，不连真实 Tushare/LLM/DB。
"""
from unittest.mock import patch
from src.llm_gateway import gateway
from src.llm_gateway.gateway import LLMResponse

fake_terms = {
    "ts_code": "113549.SH",
    "bond_short_name": "测试转债",
    "conv_price": 10.5,
    "coupon_rate": 0.6,
    "maturity_date": "2029-08-10",
    "redemption_clause": "股价超转股价130%可赎回",
    "put_clause": "股价低于转股价70%可回售",
}


def test_analyze_with_mock_llm():
    """mock gateway.chat，验证 LLM 解读返回 summary + raw_terms。"""
    with patch.object(gateway, "chat", return_value=LLMResponse(content="测试转债条款解读要点：转股价10.5元...")) as mock_chat:
        from src.astock_analysis.convertible_terms import analyze_convertible_terms
        r = analyze_convertible_terms(fake_terms)
    assert "测试转债" in r["summary"]
    assert r["raw_terms"]["conv_price"] == 10.5
    assert r["raw_terms"]["ts_code"] == "113549.SH"
    # 验证 caller 传了 convertible_terms
    mock_chat.assert_called_once()
    assert mock_chat.call_args.kwargs.get("caller") == "convertible_terms"


def test_analyze_empty_terms():
    """空条款返回占位，不调 LLM。"""
    from src.astock_analysis.convertible_terms import analyze_convertible_terms
    r = analyze_convertible_terms({})
    assert r["raw_terms"] == {}
    assert r["summary"]  # 非空占位
