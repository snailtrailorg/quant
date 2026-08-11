"""D5 AI 预算预警单测（#38）。

mock get_conn + get_channel，不连真实 DB/通道。
参照 test_factors 模式（直接调模块函数，不测 HTTP/TCP）。
"""
from unittest.mock import patch, MagicMock


def test_list_budget():
    """列预算配置（mock get_conn）。"""
    from src.web_api.main import list_llm_budget
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    cur = MagicMock()
    cur.fetchall.return_value = [(1, None, 100000, None, 80, True, None)]
    mock_conn.execute.return_value = cur
    with patch("src.web_api.main.get_conn", return_value=mock_conn):
        r = list_llm_budget(payload={"username": "admin", "role": "admin"})
    assert len(r) == 1
    assert r[0]["daily_token_limit"] == 100000
    assert r[0]["alert_threshold_pct"] == 80


def test_check_budget_alert():
    """超阈值（usage=9000 > limit 10000*80%=8000）-> 发告警。"""
    from src.web_api.main import check_budget_alerts
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    cur_budget = MagicMock()
    cur_budget.fetchall.return_value = [(1, None, 10000, None, 80, True)]
    cur_usage = MagicMock()
    cur_usage.fetchone.return_value = (9000,)
    mock_conn.execute.side_effect = [cur_budget, cur_usage]
    mock_channel = MagicMock()
    mock_channel.send.return_value = True
    with patch("src.web_api.main.get_conn", return_value=mock_conn), \
         patch("src.alert_notify.channel.get_channel", return_value=mock_channel):
        r = check_budget_alerts()
    assert r["checked"] >= 1
    assert len(r["alerts"]) >= 1
    assert r["alerts"][0]["sent"] is True
    mock_channel.send.assert_called_once()


def test_check_budget_no_alert():
    """未超阈值（usage=5000 < limit 8000）-> 不发告警。"""
    from src.web_api.main import check_budget_alerts
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    cur_budget = MagicMock()
    cur_budget.fetchall.return_value = [(1, None, 10000, None, 80, True)]
    cur_usage = MagicMock()
    cur_usage.fetchone.return_value = (5000,)
    mock_conn.execute.side_effect = [cur_budget, cur_usage]
    mock_channel = MagicMock()
    with patch("src.web_api.main.get_conn", return_value=mock_conn), \
         patch("src.alert_notify.channel.get_channel", return_value=mock_channel):
        r = check_budget_alerts()
    assert len(r["alerts"]) == 0
    mock_channel.send.assert_not_called()