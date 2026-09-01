"""LLM 预算检查（从 web_api.main 归位，2026-08-19 P 审——业务逻辑不寄生 HTTP 入口）。

随迁改动（P 建议）：直推 wechat_work 改走通知中心 notify(warn)——预算告警进站内铃铛，
告警链路统一（原实现绕过通知中心，铃铛里看不到预算事件）。
"""
from __future__ import annotations


def check_budget_alerts() -> dict:
    """检查所有 enabled budget，超阈值发告警。返回 {checked, alerts}（D5 #38）。"""
    from src.alert_notify.notify import notify
    from src.data_platform.db import get_conn
    alerts = []
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, provider, daily_token_limit, monthly_cost_limit, alert_threshold_pct, enabled "
            "FROM llm_budget WHERE enabled=true")
        budgets = cur.fetchall()
    for b in budgets:
        b_id, b_provider, b_daily_limit, b_monthly_cost, b_threshold, b_enabled = b
        if not b_daily_limit:
            continue
        sql = ("SELECT COALESCE(sum(input_tokens+output_tokens),0) FROM llm_usage "
               "WHERE ts::date=current_date")
        params = []
        if b_provider:
            sql += " AND provider=%s"
            params.append(b_provider)
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            today = cur.fetchone()[0] or 0
        limit = b_daily_limit * b_threshold // 100
        if today > limit:
            provider_name = b_provider or "全局"
            sent = False
            try:
                notify("warn", "system", "LLM 预算预警"
                       f"{provider_name} 今日 {today} token 超阈值 {limit}（{b_threshold}%）", code="llm.budget")
                sent = True
            except Exception:
                pass
            alerts.append({"provider": provider_name, "today_tokens": today, "limit": b_daily_limit,
                           "threshold": limit, "sent": sent})
    return {"checked": len(budgets), "alerts": alerts}
