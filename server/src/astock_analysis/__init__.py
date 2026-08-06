"""A股只读分析引擎 —— 日线选股 + 分钟级研判。

只读不下单，三重禁下单（代码层 + adapter raise + AI 工具白名单）。

用法:
    from src.astock_analysis import DailySelectionEngine
    engine = DailySelectionEngine(top_n=20)
    results = engine.run(trade_date="20260722")
    for r in results[:5]:
        print(f"{r.symbol}: 评分={r.score} 评级={r.rating} 结论={r.conclusion}")
"""

from .analysis import DailySelectionEngine, MinuteAnalysisEngine, AnalysisResult

__all__ = ["DailySelectionEngine", "MinuteAnalysisEngine", "AnalysisResult"]
