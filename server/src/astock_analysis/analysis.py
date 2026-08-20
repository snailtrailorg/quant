"""A股分析引擎 —— 日线选股 + 分钟级研判。

输出分析建议存 PG，供 Web 看板展示。实盘交易走 XTPAdapter（受三级开关控制，非本模块职责）。
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
import pandas as pd

from src.data_platform import to_vt_symbol

logger = logging.getLogger("astock_analysis")


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


# ——— 日线选股引擎（2026-08-20 项 5 重写：一档横截面，全市场一次 SQL 零 API）———

# 横截面因子注册表（配置驱动铁律：加因子=加条目，引擎零改动）。
# direction：1 越大越好 / -1 越小越好；col=横截面 SQL 输出列。
SELECTION_FACTORS: dict[str, dict] = {
    "net_mf_pct":   {"weight": 2.0, "direction": 1, "col": "net_mf_pct",   "desc": "主力净流入/流通市值"},
    "lg_flow_pct":  {"weight": 1.0, "direction": 1, "col": "lg_flow_pct",  "desc": "大单净额/流通市值"},
    "winner_rate":  {"weight": 1.5, "direction": 1, "col": "winner_rate",  "desc": "获利盘比例(cyq_perf)"},
    "ma_dev":       {"weight": 1.5, "direction": 1, "col": "ma_dev",       "desc": "均线偏离(45自然日窗≈30交易日)"},
}


class DailySelectionEngine:
    """日线选股模型（横截面版）：一档表全市场批量 → 因子 rank 归一 → 加权打分 → 排序。

    数据源全部本地（daily_basic/moneyflow/cyq_perf 一二档 + asset_static_info 清单），
    替代原逐标的打 Tushare API（50 只上限+每只 1 次调用）——U 审项 5 原意。
    ST/退市过滤在 SQL 层（asset 名含 ST/退剔除）。
    """

    def __init__(self, top_n: int = 30, max_stocks: int | None = None):
        self.top_n = top_n
        self._max_stocks = max_stocks or 6000   # 防呆上限（>全市场 5533；O 审 S2：曾 5000 实际截断 533 只）

    _XSECTION_SQL = """
    WITH latest AS (
        SELECT MAX(trade_date) AS dd,
               to_char(MAX(trade_date), 'YYYYMMDD') AS ds
        FROM daily_basic
        WHERE trade_date <= COALESCE(%(snap_date)s::date, '2999-12-31'::date)
    ),
    ma AS (
        SELECT ts_code, AVG(close) AS ma20, COUNT(*) AS n,
               MIN(close) AS lo20, MAX(close) AS hi20
        FROM daily_basic, latest
        WHERE trade_date >= (latest.dd - 45) AND trade_date <= latest.dd
        GROUP BY ts_code
    )
    SELECT db.ts_code, a.name, a.industry,
           to_char(latest.dd, 'YYYYMMDD') AS snap,
           db.close, db.turnover_rate,
           db.total_mv, db.circ_mv,
           (db.close / NULLIF(ma.ma20, 0) - 1) AS ma_dev,
           ma.lo20, ma.hi20,
           mf.net_mf_amount / NULLIF(db.circ_mv, 0) AS net_mf_pct,
           (mf.buy_lg_amount - mf.sell_lg_amount) / NULLIF(db.circ_mv, 0) AS lg_flow_pct,
           cp.winner_rate
    FROM daily_basic db
    JOIN latest ON db.trade_date = latest.dd
    JOIN ma ON ma.ts_code = db.ts_code AND ma.n >= 10
    JOIN asset_static_info a ON a.ts_code = db.ts_code
         AND a.name NOT LIKE '%%ST%%' AND a.name NOT LIKE '%%退%%'
    LEFT JOIN moneyflow mf ON mf.ts_code = db.ts_code AND mf.trade_date = latest.ds
    LEFT JOIN cyq_perf cp ON cp.ts_code = db.ts_code AND cp.trade_date = latest.ds
    WHERE db.close > 0 AND db.circ_mv > 0
    -- 金额单位全为万元（moneyflow 与 circ_mv 同单位直除——2026-08-20 生产实证：
    -- 曾 ÷circ_mv*10000 多除 1 万倍致原值恒 0.0000；rank 打分不受单调变换影响）
    """

    def run(self, trade_date: str | None = None) -> list[AnalysisResult]:
        """横截面选股。trade_date=YYYYMMDD 历史快照（该日截面）；None=最新日。

        O 盲审修正（2026-08-20）：
        - S1 行级缺因子：按该行可用因子重分配权重（行级 weighted mean）——
          个别行 LEFT JOIN 落空不再 NaN 沉底静默剔除
        - S2 全量无截断：A 股 5533 > 旧上限 5000 曾被任意截断且无 ORDER BY 不确定
        - S3 trade_date 显式支持：SQL 取 <= 所传日的最新截面（原被静默忽略标签错位）
        - G5 裸连接：cursor.fetchall+DataFrame（同 db.py 模式，pd.read_sql 对池化
          连接发 UserWarning）
        - G7 因子跳过告警：数据面退化（如 cyq_perf 未同步）可见
        """
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(self._XSECTION_SQL,
                            {"snap_date": trade_date})   # None→COALESCE 不限
                cols = [d.name for d in cur.description]
                df = pd.DataFrame(cur.fetchall(), columns=cols)
        if df.empty:
            return []
        df = df.head(self._max_stocks)

        # 因子 rank(pct) 归一 [-1,1]；列级缺数（notna<30）跳过+告警；行级按可用因子重分配权重
        score = pd.Series(0.0, index=df.index)
        w_sum = pd.Series(0.0, index=df.index)
        for name, spec in SELECTION_FACTORS.items():
            col = df.get(spec["col"])
            if col is None or col.notna().sum() < 30:
                logger.warning("选股因子 %s 数据不足（notna=%s）——本轮不参与打分",
                               name, 0 if col is None else int(col.notna().sum()))
                continue
            r = col.rank(pct=True).sub(0.5).mul(2 * spec["direction"])
            valid = r.notna()
            score = score + r.fillna(0.0) * spec["weight"] * valid
            w_sum = w_sum + spec["weight"] * valid
        usable = w_sum > 0
        if not usable.any():
            return []
        score = (score / w_sum)[usable]
        df = df[usable]

        df = df.assign(score=score.round(3)).sort_values("score", ascending=False)

        # rating 分位在 top_n 内算（O 审 G4：全市场分位使 top30 恒 BUY 无区分度）
        top = df.head(self.top_n)
        q_hi, q_lo = top["score"].quantile(0.85), top["score"].quantile(0.15)
        results = []
        for _, row in top.iterrows():
            rating = "BUY" if row["score"] >= q_hi else "AVOID" if row["score"] <= q_lo else "HOLD"
            ts_code = row["ts_code"]
            # conclusion 用因子原值（O 审 G9：rank 是分位不是量纲，原值才可读）
            fv = {k: (None if pd.isna(row.get(spec["col"])) else float(row[spec["col"]]))
                  for k, spec in SELECTION_FACTORS.items()}
            conclusion = (f"{row['name']}({row['industry']}) 收{row['close']:.2f} "
                          f"评分={row['score']:.3f}; " + ", ".join(
                              f"{k}={v:.4f}" if v is not None else f"{k}=缺"
                              for k, v in fv.items()))
            results.append(AnalysisResult(
                ts=row.get("snap") or trade_date or "",
                symbol=ts_code,
                vt_symbol=to_vt_symbol(ts_code),
                score=float(row["score"]),
                rating=rating,
                factors=fv,
                support=round(float(row["lo20"] or 0), 2),
                resistance=round(float(row["hi20"] or 0), 2),
                conclusion=conclusion,
            ))
        return results

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
                ], role="viewer", caller="astock")
                if resp and resp.content:
                    r.llm_summary = resp.content[:200]
            except Exception:
                r.llm_summary = "（LLM 暂不可用）"
        return results


# ——— 分钟级研判引擎（占位，T10 后续实现） ———

class MinuteAnalysisEngine:
    """盘中分钟级研判模型（on_bar 已实现，D2 2026-08-10）。

    实时订阅 1min/5min K 线 → 因子计算 → 信号 → 推送 Web 看板。
    实时行情订阅（tick→BarGenerator→on_bar）属 #4 实盘化范畴。
    """
    def __init__(self):
        pass

    def on_bar(self, bar: dict, history: list[dict] | None = None) -> dict:
        """收到分钟 K 线 实时研判（bar + history 因子计算）。

        bar: {ts, open, high, low, close, volume}（1min/5min）
        history: 过去 bar 列表（构建因子上下文，防未来函数；None 时只用当前 bar）
        返回 {"action", "score", "rating", "conclusion", "factors"}
        """
        history = history or []
        close = bar.get("close", 0)
        volume = bar.get("volume", 0)

        # sma_20: 最近 20 根收盘均值（含当前 close）
        closes = [h.get("close", 0) for h in history] + [close]
        window = closes[-20:]
        sma_20 = sum(window) / len(window) if window else close
        ma_dev = (close / sma_20 - 1) if sma_20 else 0

        # momentum: 相对 history 首根（空时 0）
        first_close = history[0].get("close") if history else None
        momentum = (close / first_close - 1) if first_close else 0

        # vol_ratio: 当前量 / 近 5 根均量（history<5 时 1）
        recent_vols = [h.get("volume", 0) for h in history[-5:]]
        vol_ratio = (volume / (sum(recent_vols) / len(recent_vols) + 1)) if recent_vols else 1

        score = ma_dev * 2 + momentum * 1.5 + vol_ratio * 0.5
        rating = "BUY" if score > 0.3 else "AVOID" if score < -0.3 else "HOLD"
        action = {"BUY": "BUY", "AVOID": "SELL", "HOLD": "HOLD"}[rating]

        conclusion = (f"均线偏离={ma_dev:.3f}, 动量={momentum:.3f}, "
                      f"量比={vol_ratio:.2f}, 综合评分={score:.3f}")

        return {
            "action": action,
            "score": round(score, 3),
            "rating": rating,
            "conclusion": conclusion,
            "factors": {"ma_dev": round(ma_dev, 3), "momentum": round(momentum, 3),
                        "vol_ratio": round(vol_ratio, 2)},
        }