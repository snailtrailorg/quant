"""LLM 用量 series API 单测（批54 追加三单档终态——mock 可控数据）。
史:批50 真库版(空库假绿)→批54 两档 mock 版→追加三 gran 参数退役固定 day。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from unittest.mock import patch, MagicMock
from datetime import datetime


def _conn_with(*fetchalls):
    """单 with 三查按序（today/时序/网格）。"""
    c = MagicMock(); c.__enter__.return_value = c
    if len(fetchalls) == 1:
        cur = MagicMock(); cur.fetchall.return_value = fetchalls[0]
        c.execute.return_value = cur
    else:
        cursors = []
        for rows in fetchalls:
            cur = MagicMock(); cur.fetchall.return_value = rows
            cursors.append(cur)
        c.execute.side_effect = cursors
    return c


def test_series_day_grid_and_today():
    """固定 day 全历史:网格=数据 min 到今天补零+今日汇总+双键。"""
    from src.web_api.routes.chat import llm_usage_series
    today_rows = [("deepseek", "m1", 5, 100, 100.0)]
    day_rows = [("deepseek", "m1", datetime(2026, 9, 15), 1, 50)]
    grid_rows = [("2026-09-15",), ("2026-09-16",), ("2026-09-17",)]
    with patch("src.web_api.routes.chat.get_conn", return_value=_conn_with(today_rows, day_rows, grid_rows)):
        r = llm_usage_series(payload={"username": "t"})
    assert len(r["models"]) == 1
    m = r["models"][0]
    assert m["today"] == {"calls": 5, "tokens": 100, "success_rate": 100.0}
    assert len(m["series"]) == 3 and m["series"][0]["ts"] == "2026-09-15"
    assert m["series"][0]["calls"] == 1 and m["series"][1]["calls"] == 0   # 补零
    assert all({"ts", "calls", "tokens"} == set(p) for p in m["series"])


def test_series_empty():
    """零数据:models 空(前端空态)。"""
    from src.web_api.routes.chat import llm_usage_series
    with patch("src.web_api.routes.chat.get_conn", return_value=_conn_with([], [], [])):
        r = llm_usage_series(payload={"username": "t"})
    assert r["models"] == []
