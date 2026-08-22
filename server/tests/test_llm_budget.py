"""D5 AI 预算预警单测（#38）。

mock get_conn + get_channel，不连真实 DB/通道。
参照 test_factors 模式（直接调模块函数，不测 HTTP/TCP）。
"""
from unittest.mock import patch, MagicMock


def test_list_budget():
    """列预算配置（mock get_conn）。"""
    from src.web_api.routes.chat import list_llm_budget
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    cur = MagicMock()
    cur.fetchall.return_value = [(1, None, 100000, None, 80, True, None)]
    mock_conn.execute.return_value = cur
    with patch("src.web_api.routes.chat.get_conn", return_value=mock_conn):
        r = list_llm_budget(payload={"username": "admin", "role": "admin"})
    assert len(r) == 1
    assert r[0]["daily_token_limit"] == 100000
    assert r[0]["alert_threshold_pct"] == 80


def test_check_budget_alert():
    """超阈值（usage=9000 > limit 10000*80%=8000）-> 发告警。"""
    from src.llm_gateway.budget import check_budget_alerts
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    cur_budget = MagicMock()
    cur_budget.fetchall.return_value = [(1, None, 10000, None, 80, True)]
    cur_usage = MagicMock()
    cur_usage.fetchone.return_value = (9000,)
    mock_conn.execute.side_effect = [cur_budget, cur_usage]
    # 2026-08-19 归位后语义（P 建议）：预算告警走通知中心 notify（进站内铃铛），不再直推渠道
    with patch("src.data_platform.db.get_conn", return_value=mock_conn), \
         patch.object(__import__("src.alert_notify.notify", fromlist=["notify"]), "notify") as mock_notify:
        r = check_budget_alerts()
    assert r["checked"] >= 1
    assert len(r["alerts"]) >= 1
    assert r["alerts"][0]["sent"] is True
    mock_notify.assert_called_once()
    assert mock_notify.call_args.args[0] == "warn"


def test_check_budget_no_alert():
    """未超阈值（usage=5000 < limit 8000）-> 不发告警。"""
    from src.llm_gateway.budget import check_budget_alerts
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    cur_budget = MagicMock()
    cur_budget.fetchall.return_value = [(1, None, 10000, None, 80, True)]
    cur_usage = MagicMock()
    cur_usage.fetchone.return_value = (5000,)
    mock_conn.execute.side_effect = [cur_budget, cur_usage]
    with patch("src.data_platform.db.get_conn", return_value=mock_conn), \
         patch.object(__import__("src.alert_notify.notify", fromlist=["notify"]), "notify") as mock_notify:
        r = check_budget_alerts()
    assert len(r["alerts"]) == 0
    mock_notify.assert_not_called()