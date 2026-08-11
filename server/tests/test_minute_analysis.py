"""D2 MinuteAnalysisEngine.on_bar 单测（#32）。

纯函数测，不连 DB/LLM。4 例：涨/跌/震荡/history=None。
"""
from src.astock_analysis.analysis import MinuteAnalysisEngine


def test_on_bar_up():
    """放量上涨 -> BUY。"""
    eng = MinuteAnalysisEngine()
    hist = [{"close": 9 + i * 0.1, "volume": 100} for i in range(20)]
    r = eng.on_bar(
        {"ts": "2026-08-10T09:31", "open": 11, "high": 11.2, "low": 10.9,
         "close": 11.1, "volume": 200},
        history=hist,
    )
    assert r["rating"] == "BUY"
    assert r["action"] == "BUY"
    assert "ma_dev" in r["factors"]
    assert r["score"] > 0.3


def test_on_bar_down():
    """缩量下跌 -> AVOID/SELL。"""
    eng = MinuteAnalysisEngine()
    hist = [{"close": 11 - i * 0.1, "volume": 100} for i in range(20)]
    r = eng.on_bar(
        {"open": 9, "high": 9.1, "low": 8.8, "close": 8.9, "volume": 10},
        history=hist,
    )
    assert r["rating"] == "AVOID"
    assert r["action"] == "SELL"
    assert r["score"] < -0.3


def test_on_bar_flat():
    """缩量平盘 -> HOLD。"""
    eng = MinuteAnalysisEngine()
    hist = [{"close": 10, "volume": 100} for _ in range(20)]
    r = eng.on_bar(
        {"close": 10, "open": 10, "high": 10, "low": 10, "volume": 50},
        history=hist,
    )
    assert r["rating"] == "HOLD"
    assert r["action"] == "HOLD"
    assert -0.3 <= r["score"] <= 0.3


def test_on_bar_no_history():
    """history=None 不崩，只用当前 bar。"""
    eng = MinuteAnalysisEngine()
    r = eng.on_bar({"close": 10, "open": 10, "high": 10, "low": 10, "volume": 100})
    assert "score" in r
    assert "factors" in r
    assert r["action"] in ("BUY", "SELL", "HOLD")
    assert r["rating"] in ("BUY", "HOLD", "AVOID")
