"""LLM 用量 series API 单测（批50 起/批54 两档重写——mock 形态可控数据，原真库版在空库时
models=[] 循环跳过断言=假绿）。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from unittest.mock import patch, MagicMock


def _conn_with(*fetchalls):
    """单 with 三查按序（today/时序/网格——series 端点一个 with 块三连查）。
    兼容旧单列表形态=_conn_with(rows)=三查同返。"""
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


def test_series_hour_shape():
    """hour 档（默认）：168h 网格补零+双键。"""
    from src.web_api.routes.chat import llm_usage_series
    # 三段查询:today 聚合/时序聚合/网格 generate_series——简化:统一 mock 返回按序
    today_rows = [("deepseek", "m1", 5, 100, 100.0)]
    from datetime import datetime
    hour_rows = [("deepseek", "m1", datetime(2026, 9, 15, 10), 1, 50)]   # bucket=datetime
    grid_rows = [(f"2026-09-{d:02d}T{h:02d}:00",) for d in range(10, 17) for h in range(24)]
    conns = [_conn_with(today_rows, hour_rows, grid_rows)]
    with patch("src.web_api.routes.chat.get_conn", side_effect=conns):
        r = llm_usage_series(granularity="hour", payload={"username": "t"})
    assert r["granularity"] == "hour"
    assert len(r["models"]) == 1 and len(r["models"][0]["series"]) == 168   # 7 天网格
    assert all({"ts", "calls", "tokens"} == set(p) for p in r["models"][0]["series"])


def test_series_day_and_whitelist():
    """day 档网格 90+畸形 granularity 归一 hour。"""
    from src.web_api.routes.chat import llm_usage_series
    grid_rows = [(f"2026-{m:02d}-{d:02d}",) for m in range(6, 10) for d in range(1, 31)]
    conns = [_conn_with([], [], grid_rows[:90])]
    with patch("src.web_api.routes.chat.get_conn", side_effect=conns):
        r = llm_usage_series(granularity="day", payload={"username": "t"})
    assert r["granularity"] == "day" and len(r["models"]) == 0   # 无 today 无时序=零模型
    conns = [_conn_with([], [], [])]
    with patch("src.web_api.routes.chat.get_conn", side_effect=conns):
        r2 = llm_usage_series(granularity="week", payload={"username": "t"})   # 畸形值归一
    assert r2["granularity"] == "hour"
