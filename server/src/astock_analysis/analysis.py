"""A股分析引擎 —— 日线选股 + 分钟级研判。

输出分析建议存 PG，供 Web 看板展示。实盘交易走 XTPAdapter（受三级开关控制，非本模块职责）。
"""

from __future__ import annotations
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import Any
import pandas as pd

from src.strategy_framework import (
    Strategy, StrategyConfig, Signal, Action, BarContext, SignalAggregator,
    create_adapter, list_factors, register_factor, get_factor,
)
from src.data_platform import platform, to_vt_symbol, parse_vt_symbol


@dataclass
class AnalysisResult:
    """A股分析输出。"""
    ts: str
    symbol: str
    vt_symbol: str
    model: str = "daily_select_v1"
    score: float = 0.0
    rating: str = "HOLD"      # BUY / HOLD / AVOID
    factors: dict = field(default_factory=dict)
    support: float = 0.0
    resistance: float = 0.0
    conclusion: str = ""
    llm_summary: str = ""


# ——— 日线选股引擎 ———

class DailySelectionEngine:
    """日线选股模型：多因子打分 → 排序 → 输出。"""

    def __init__(self, top_n: int = 30):
        self.top_n = top_n
        # 注册 A 股因子
        self._factors = self._register_astock_factors()

    def _register_astock_factors(self) -> list[dict]:
        """注册 A 股专用因子。"""
        # 复用已有因子
        factors = []
        for name in ["ma_dev", "rsi", "volume_ratio"]:
            entry = get_factor(name)
            if entry:
                factors.append(entry)
        return factors

    def run(self, trade_date: str | None = None) -> list[AnalysisResult]:
        """运行日线选股，返回排名结果。"""
        from src.data_platform.adapters.tushare_adapter import get_pro

        trade_date = trade_date or date.today().strftime("%Y%m%d")
        pro = get_pro()

        # 1. 获取股票列表
        stocks = self._get_stock_list(pro)
        if not stocks:
            return []

        # 2. 获取日线数据（批量）
        results = []
        for ts_code in stocks[:50]:  # 先限 50 只测试，后续全量
            try:
                result = self._analyze_single(pro, ts_code, trade_date)
                if result:
                    results.append(result)
            except Exception:
                continue

        # 3. 排序输出
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:self.top_n]

    def _get_stock_list(self, pro) -> list[str]:
        """获取 A 股列表。"""
        try:
            df = pro.query("stock_basic", exchange="", list_status="L",
                           fields="ts_code,name,industry")
            return df["ts_code"].tolist() if df is not None and not df.empty else []
        except Exception:
            return []

    def _analyze_single(self, pro, ts_code: str, trade_date: str) -> AnalysisResult | None:
        """分析单只股票。"""
        import numpy as np

        # 拉近 60 天日线
        start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
        try:
            df = pro.daily(ts_code=ts_code, start_date=start, end_date=trade_date)
        except Exception:
            return None
        if df is None or df.empty or len(df) < 20:
            return None

        df = df.sort_values("trade_date")
        close = df["close"].values
        volume = df["vol"].values
        latest = df.iloc[-1]

        # 计算因子
        sma_20 = np.mean(close[-20:]) if len(close) >= 20 else close[-1]
        ma_dev = latest["close"] / sma_20 - 1

        # 动量
        momentum = (latest["close"] / close[0] - 1) if len(close) > 1 else 0

        # 成交量比
        vol_ratio = latest["vol"] / (np.mean(volume[-5:]) + 1) if len(volume) >= 5 else 1

        # 复合评分
        score = ma_dev * 2 + momentum * 1.5 + vol_ratio * 0.5

        # 支撑/阻力（简单：近 20 日高低）
        support = float(np.min(close[-20:])) if len(close) >= 20 else float(latest["low"])
        resistance = float(np.max(close[-20:])) if len(close) >= 20 else float(latest["high"])

        # 评级
        rating = "BUY" if score > 0.3 else "AVOID" if score < -0.3 else "HOLD"

        vt_sym = to_vt_symbol(ts_code)
        conclusion = (f"均线偏离={ma_dev:.3f}, 动量={momentum:.3f}, "
                      f"量比={vol_ratio:.2f}, 综合评分={score:.3f}")

        return AnalysisResult(
            ts=trade_date,
            symbol=ts_code,
            vt_symbol=vt_sym,
            score=round(score, 3),
            rating=rating,
            factors={"ma_dev": round(ma_dev, 3), "momentum": round(momentum, 3),
                     "vol_ratio": round(vol_ratio, 2)},
            support=round(support, 2),
            resistance=round(resistance, 2),
            conclusion=conclusion,
        )

    def enhance_with_llm(self, results: list[AnalysisResult]) -> list[AnalysisResult]:
        """用 LLM 增强分析——为高评分股票生成自然语言研判（需 LLM 网关可用）。"""
        try:
            from src.llm_gateway import gateway
        except ImportError:
            return results

        for r in results[:5]:  # 只分析前 5 只
            try:
                resp = gateway.chat([
                    {"role": "system", "content": "你是一个 A 股分析助手，输出简洁的个股研判。用中文回复。"},
                    {"role": "user", "content": f"分析 {r.symbol}：{r.conclusion}。评分 {r.score}，评级 {r.rating}。"
                     f"支撑 {r.support}，阻力 {r.resistance}。给出简要研判。"}
                ], tier="regular", role="viewer")
                if resp and resp.content:
                    r.llm_summary = resp.content[:200]
            except Exception:
                r.llm_summary = "（LLM 暂不可用）"
        return results


# ——— 分钟级研判引擎（占位，T10 后续实现） ———

class MinuteAnalysisEngine:
    """盘中分钟级研判模型。

    实时订阅 1min/5min K 线 → 因子计算 → 信号 → 推送 Web 看板。
    T10 实现 on_bar 实时处理，目前占位。
    """
    def __init__(self):
        pass

    def on_bar(self, bar: dict) -> dict:
        """收到分钟 K 线 → 实时研判。"""
        from src.strategy_framework import BarContext
        ctx = BarContext(
            close=bar.get("close", 0), high=bar.get("high", 0),
            low=bar.get("low", 0), open_=bar.get("open", 0),
            volume=bar.get("volume", 0),
        )
        return {"action": "HOLD", "signal": "暂未实现"}