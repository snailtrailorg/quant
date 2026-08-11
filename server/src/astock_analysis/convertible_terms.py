"""AI 可转债条款解读（D3 #33）。

LLM 解读可转债基本信息（转股价/利率/赎回/回售条款）-> 自然语言投资要点。
caller="convertible_terms"。条款数据由 tushare_adapter.pull_cb_basic 拉取。
"""


def analyze_convertible_terms(terms: dict) -> dict:
    """LLM 解读可转债条款。

    Args:
        terms: pull_cb_basic 返回的 dict（含 ts_code/bond_short_name/conv_price/
               coupon_rate/maturity_date/redemption_clause/put_clause 等）

    Returns:
        {"summary": str, "raw_terms": dict}
        summary: LLM 解读文本（LLM 不可用时占位）
        raw_terms: 原始条款 dict（供前端展示）
    """
    if not terms:
        return {"summary": "（无条款数据）", "raw_terms": {}}
    from src.llm_gateway import gateway

    name = terms.get("bond_short_name") or terms.get("ts_code", "")
    prompt = (
        f"可转债 {name} 条款：转股价={terms.get('conv_price')}, "
        f"利率={terms.get('coupon_rate')}, 到期={terms.get('maturity_date')}, "
        f"赎回条款={terms.get('redemption_clause')}, "
        f"回售条款={terms.get('put_clause')}. 请解读投资要点与风险。"
    )
    try:
        resp = gateway.chat(
            messages=[
                {"role": "system", "content": "你是可转债分析助手，解读条款要点（转股价/利率/赎回/回售），用中文简洁回复"},
                {"role": "user", "content": prompt},
            ],
            role="viewer",
            caller="convertible_terms",
        )
        summary = resp.content if resp and resp.content else "（LLM 无响应，请检查 API key）"
    except Exception as e:
        summary = f"（LLM 暂不可用: {e}）"
    return {"summary": summary[:500], "raw_terms": terms}
