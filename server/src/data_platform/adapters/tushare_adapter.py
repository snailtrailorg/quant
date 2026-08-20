"""数据中台 · Tushare 数据源适配器。

Tushare Pro 为 A 股/可转债/ETF 日线主数据源。
分钟线需 2000 积分，当前先用日线（token 已验通）。
"""

from __future__ import annotations
import os
import pandas as pd
from typing import Any
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()


def _safe_float(v, default: float = 0.0) -> float:
    """安全转 float：None/NaN/空串 -> default。避免早期历史数据缺字段 float(None) 崩溃。"""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    # pd.NA / nan 检测
    try:
        if pd.isna(f):
            return default
    except (TypeError, ValueError):
        pass
    return f

# ——— 全局 Tushare API ———

_pro = None

def get_pro():
    global _pro
    if _pro is None:
        import tushare as ts
        token = os.environ.get("TUSHARE_TOKEN", "")
        _pro = ts.pro_api(token)
    return _pro


# ——— 复权因子（2026-08-18 多频盲审 A/B-F1：bar_1D 契约=未复权价+逐行 adj_factor）———

_adj_degraded = {"ts": 0.0}   # 进程级降级标记（每小时最多告警一次，避免轰炸）


def _adj_degraded_alert(e: Exception) -> None:
    """因子接口降级告警（限频）：积分未到账/接口异常时，日线同步继续（因子 NULL），
    不崩——积分到账后下次同步或手动回补自动恢复。"""
    import time as _time, logging as _logging
    now = _time.time()
    if now - _adj_degraded["ts"] < 3600:
        return
    _adj_degraded["ts"] = now
    _logging.getLogger("tushare_adapter").warning("adj_factor 接口降级（积分未到账/接口异常），日线同步继续无因子: %s", e)
    try:
        from src.alert_notify.notify import notify
        notify("warn", "system", "复权因子接口降级",
               "Tushare adj_factor 不可用（积分未到账或接口异常）。日线同步继续（因子 NULL），"
               "跨除权日因子暂不可用；积分到账后触发手动回补即可恢复。")
    except Exception:
        pass


def pull_adj_factor_by_date(trade_date: str) -> pd.DataFrame | None:
    """全市场单日复权因子（pro.adj_factor trade_date 模式，与按日全市场日线同步同构）。

    Returns:
        DataFrame[ts_code, trade_date, adj_factor]；当日无数据返回空 df；
        **接口降级（权限/异常）返回 None**——调用方按"因子缺失"处理，同步绝不因此中断。
    F 评审修订：1h 闩锁——降级确认后本进程 1h 内直接返回 None 不再打失败接口
    （省 Tushare 配额 + 降级期同步提速），积分到账后最长 1h 自动重试。
    """
    import time as _t
    if _t.time() - _adj_degraded["ts"] < 3600:
        return None
    pro = get_pro()
    try:
        df = pro.adj_factor(trade_date=trade_date)
        if df is None or df.empty:
            return pd.DataFrame()
        return df[["ts_code", "trade_date", "adj_factor"]]
    except Exception as e:
        _adj_degraded_alert(e)
        return None


def pull_adj_factor_by_code(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """单标的区间复权因子（修复路径用）。降级语义同 pull_adj_factor_by_date。"""
    pro = get_pro()
    try:
        df = pro.adj_factor(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        return df[["ts_code", "trade_date", "adj_factor"]]
    except Exception as e:
        _adj_degraded_alert(e)
        return None


# ——— 日线拉取 ———

def get_daily_symbols() -> list[str]:
    """获取当前可交易的 A 股/可转债/ETF 列表（含上市状态过滤）。

    返回 Tushare ts_code 列表，如 ['600000.SH', '113549.SH', …]。
    """
    pro = get_pro()
    all_codes = []
    # 股票
    df = pro.query("stock_basic", exchange="", list_status="L", fields="ts_code")
    all_codes.extend(df["ts_code"].tolist())
    return all_codes


def pull_daily(ts_code: str, start_date: str, end_date: str | None = None,
               adj: str | None = None) -> pd.DataFrame:
    """从 Tushare 拉取单只标的日线。

    Args:
        ts_code: Tushare 代码，如 "600000.SH"
        start_date: 开始日期 "YYYYMMDD"
        end_date: 结束日期，默认今天
        adj: 复权类型（默认 **None 不复权**——2026-08-18 多频盲审 A-F1：bar_1D 契约=未复权价+逐行
             adj_factor，修复路径曾用 qfq 价混入同表造成口径跳变；复权在计算侧用因子做）

    Returns:
        DataFrame 列: ts_code, trade_date, open, high, low, close, pre_close,
                      change, pct_chg, vol, amount, adj_factor
    """
    pro = get_pro()
    end_date = end_date or date.today().strftime("%Y%m%d")
    # Tushare pro_bar 或 daily 接口
    # daily 接口不支持复权, pro_bar 支持
    try:
        df = pro.pro_bar(
            ts_code=ts_code, freq="D",
            start_date=start_date, end_date=end_date,
            adj=adj,  # qfq/hfq/None
        )
    except Exception:
        # fallback 到 daily（不复权）
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)

    if df is None or df.empty:
        return pd.DataFrame()

    # 补齐字段
    if "adj_factor" not in df.columns:
        df["adj_factor"] = None
    return df


def pull_cb_daily(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """拉取可转债日线全量（cb_daily 接口）。"""
    pro = get_pro()
    end_date = end_date or date.today().strftime("%Y%m%d")
    df = pro.cb_daily(start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        return pd.DataFrame()
    df["adj_factor"] = None
    return df


def pull_cb_basic(ts_code: str) -> dict:
    """拉单只可转债基本信息（条款字段，cb_basic 接口）。

    返回 df.iloc[0].to_dict()（含 ts_code/bond_short_name/conv_price/coupon_rate/
    maturity_date/redemption_clause/put_clause 等）。失败或空返回 {}（不抛）。
    """
    try:
        pro = get_pro()
        df = pro.cb_basic(ts_code=ts_code)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    return df.iloc[0].to_dict()


def pull_minute(ts_code: str, freq: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """拉取分钟线（stk_mins 接口，需 2000 积分）。

    Args:
        ts_code: Tushare 代码，如 "600000.SH"
        freq: "1min" / "5min" / "15min" / "30min" / "60min"
        start_date: "YYYYMMDD HH:MM:SS"
        end_date: 同上，默认当天
    """
    pro = get_pro()
    end_date = end_date or (date.today().strftime("%Y%m%d") + " 15:00:00")
    df = pro.stk_mins(ts_code=ts_code, freq=freq,
                      start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        return pd.DataFrame()
    # stk_mins 列: ts_code, trade_time, open, high, low, close, vol, amount
    df["adj_factor"] = None
    # 加 trade_date 列（从 trade_time 提取 YYYYMMDD，便于统一处理）
    df["trade_date"] = df["trade_time"].str[:10].str.replace("-", "")
    return df


def to_save_rows_min(df: pd.DataFrame, freq: str) -> list[tuple]:
    """分钟线 DataFrame → 写入 DB 行列表（trade_time 作为 ts）。"""
    from ..schema import to_vt_symbol
    rows = []
    for _, row in df.iterrows():
        ts_code = row.get("ts_code", "")
        vt_sym = to_vt_symbol(ts_code)
        # trade_time 格式 "YYYY-MM-DD HH:MM:SS"
        ts = pd.Timestamp(row["trade_time"]).to_pydatetime()
        rows.append((
            vt_sym, freq, ts,
            _safe_float(row["open"]), _safe_float(row["high"]), _safe_float(row["low"]),
            _safe_float(row["close"]),
            _safe_float(row.get("vol", 0)), _safe_float(row.get("amount", 0)),
            _safe_float(row["adj_factor"]) if row.get("adj_factor") and pd.notna(row["adj_factor"]) else None,
            "tushare",
        ))
    return rows


def to_save_rows(df: pd.DataFrame, freq: str = "1D") -> list[tuple]:
    """DataFrame → 写入 DB 的行列表。"""
    from ..schema import to_vt_symbol

    rows = []
    for _, row in df.iterrows():
        ts_code = row.get("ts_code", "")
        vt_sym = to_vt_symbol(ts_code)
        trade_date = pd.Timestamp(row["trade_date"]).to_pydatetime()
        rows.append((
            vt_sym, freq, trade_date,
            _safe_float(row["open"]), _safe_float(row["high"]), _safe_float(row["low"]),
            _safe_float(row["close"]),
            _safe_float(row.get("vol", 0)), _safe_float(row.get("amount", 0)),
            _safe_float(row["adj_factor"]) if row.get("adj_factor") and pd.notna(row["adj_factor"]) else None,
            "tushare",
        ))
    return rows


# ——— 交易日历 ———

def pull_trade_cal(year: int) -> list[tuple]:
    """拉取 A 股交易日历并写入 DB。"""
    from ..db import init_trade_calendar, get_conn
    from ..db import ensure_table  # noqa

    pro = get_pro()
    df = pro.trade_cal(exchange="SSE", start_date=f"{year}0101", end_date=f"{year}1231")
    if df is None or df.empty:
        return []

    init_trade_calendar(year)
    rows = []
    # DB 优化（2026-08-21 盘点）：全仓唯一不带 with 的连接——泄漏实锤，改池化用法
    from .db import get_conn as _get_pooled_conn
    with _get_pooled_conn() as pro:
        with pro.cursor() as cur:
            for _, r in df.iterrows():
                rows.append((
                    r["exchange"], r["cal_date"], int(r["is_open"]),
                    r.get("pretrade_date"),
                ))
            cur.executemany(
                "INSERT INTO trade_cal (exchange, cal_date, is_open, pretrade_date) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (exchange, cal_date) DO NOTHING",
                rows,
            )
            pro.commit()
    return rows


# ——— 可转债/ETF ———

def pull_convertible_bonds() -> list[str]:
    """获取可转债列表（ts_code）。"""
    pro = get_pro()
    df = pro.cb_basic(fields="ts_code")
    return df["ts_code"].tolist() if df is not None and not df.empty else []


def pull_etf_list() -> list[str]:
    """获取 ETF 列表（ts_code）。"""
    pro = get_pro()
    df = pro.fund_basic(market="E", fields="ts_code")
    return df["ts_code"].tolist() if df is not None and not df.empty else []

# --- 财务指标（daily_basic） ---

def pull_daily_basic(ts_code: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """拉取每日基本面指标（PE/PB/换手率/市值等）。需 Tushare 积分。

    Returns: DataFrame with ts_code, trade_date, close, turnover_rate, pe, pe_ttm,
             pb, ps, ps_ttm, dv_ratio, total_mv, circ_mv, ...
    """
    pro = get_pro()
    end_date = end_date or date.today().strftime("%Y%m%d")
    df = pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        return pd.DataFrame()
    return df


DAILY_BASIC_DDL = """
CREATE TABLE IF NOT EXISTS daily_basic (
    id BIGSERIAL PRIMARY KEY,
    ts_code TEXT NOT NULL,
    vt_symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    close NUMERIC,
    turnover_rate NUMERIC,
    pe NUMERIC,
    pe_ttm NUMERIC,
    pb NUMERIC,
    ps NUMERIC,
    ps_ttm NUMERIC,
    dv_ratio NUMERIC,
    dv_ttm NUMERIC,
    total_mv NUMERIC,
    circ_mv NUMERIC,
    UNIQUE(ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_basic_code_date ON daily_basic (ts_code, trade_date DESC);
"""

DAILY_BASIC_INSERT = """
INSERT INTO daily_basic (ts_code, vt_symbol, trade_date, close, turnover_rate, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm, total_mv, circ_mv)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (ts_code, trade_date) DO NOTHING
"""


def save_daily_basic(df: pd.DataFrame) -> int:
    """存 daily_basic 到 PG。"""
    from ..db import get_conn
    from ..schema import to_vt_symbol
    if df.empty:
        return 0
    rows = []
    for _, r in df.iterrows():
        rows.append((
            r["ts_code"], to_vt_symbol(r["ts_code"]),
            pd.Timestamp(r["trade_date"]).to_pydatetime().date(),
            _safe_float(r.get("close")), _safe_float(r.get("turnover_rate")),
            _safe_float(r.get("pe")) if pd.notna(r.get("pe")) else None,
            _safe_float(r.get("pe_ttm")) if pd.notna(r.get("pe_ttm")) else None,
            _safe_float(r.get("pb")) if pd.notna(r.get("pb")) else None,
            _safe_float(r.get("ps")) if pd.notna(r.get("ps")) else None,
            _safe_float(r.get("ps_ttm")) if pd.notna(r.get("ps_ttm")) else None,
            _safe_float(r.get("dv_ratio")) if pd.notna(r.get("dv_ratio")) else None,
            _safe_float(r.get("dv_ttm")) if pd.notna(r.get("dv_ttm")) else None,
            _safe_float(r.get("total_mv")) if pd.notna(r.get("total_mv")) else None,
            _safe_float(r.get("circ_mv")) if pd.notna(r.get("circ_mv")) else None,
        ))
    with get_conn() as conn:
        # daily_basic 表已在 migration 0001 创建，不再运行时 DDL
        with conn.cursor() as cur:
            cur.executemany(DAILY_BASIC_INSERT, rows)
        conn.commit()
    return len(rows)


# --- 数据质量校验 ---

def validate_bar_quality(df: pd.DataFrame) -> dict:
    """F-DATA-020 数据质量自动校验。

    检查：去重、价量为 0、异常跳空、时序断点。
    """
    if df is None or df.empty:
        return {"valid": False, "issues": ["空数据"], "clean_count": 0}

    issues = []
    total = len(df)
    clean = df.copy()

    # 1. 去重（trade_date 重复）
    dup_count = clean.duplicated(subset=["ts_code", "trade_date"]).sum()
    if dup_count > 0:
        issues.append(f"重复K线: {dup_count} 条")
        clean = clean.drop_duplicates(subset=["ts_code", "trade_date"])

    # 2. 价/量为 0
    for col in ["open", "high", "low", "close"]:
        zero = (clean[col] == 0).sum()
        if zero > 0:
            issues.append(f"{col} 为 0: {zero} 条")
            clean = clean[clean[col] != 0]

    vol_zero = (clean["vol"] == 0).sum() if "vol" in clean.columns else 0
    if vol_zero > 0:
        issues.append(f"成交量为 0: {vol_zero} 条（停牌可能正常）")

    # 3. 异常跳空（今收 vs 昨收 > 20%）
    if "close" in clean.columns and "pre_close" in clean.columns:
        clean["gap_pct"] = abs(clean["close"] - clean["pre_close"]) / clean["pre_close"].replace(0, 1) * 100
        big_gaps = (clean["gap_pct"] > 20).sum()
        if big_gaps > 0:
            issues.append(f"异常跳空(>20%): {big_gaps} 条")

    # 4. 时序断点（日线 >7 天间隔）
    if "trade_date" in clean.columns:
        clean = clean.sort_values("trade_date")
        dates = pd.to_datetime(clean["trade_date"], format="%Y%m%d")
        diffs = dates.diff().dt.days
        gaps = (diffs > 7).sum()
        if gaps > 0:
            issues.append(f"时序断点(>7天): {gaps} 处")

    return {
        "valid": len([i for i in issues if "为 0" not in i and "停牌" not in i]) == 0,
        "issues": issues,
        "clean_count": len(clean),
        "original_count": total,
        "dedupped": int(dup_count) if dup_count > 0 else 0,
    }


# ─── 三档数据第一档：全局定时同步 pull 函数（U 审 2026-08-19）───

def pull_stk_limit(trade_date: str, end_date: str | None = None) -> pd.DataFrame:
    """每日涨跌停价格（全市场单日，盘前 08:40 Tushare 更新）。"""
    pro = get_pro()
    return pro.stk_limit(trade_date=trade_date)

def pull_moneyflow(trade_date: str, end_date: str | None = None) -> pd.DataFrame:
    """个股资金流向（全市场单日，20 列大中小单）。"""
    pro = get_pro()
    return pro.moneyflow(trade_date=trade_date)

def pull_margin_detail(trade_date: str, end_date: str | None = None) -> pd.DataFrame:
    """融资融券明细（全市场单日，T+1）。"""
    pro = get_pro()
    return pro.margin_detail(trade_date=trade_date)

def pull_top_list(trade_date: str, end_date: str | None = None) -> pd.DataFrame:
    """龙虎榜每日明细。"""
    pro = get_pro()
    return pro.top_list(trade_date=trade_date)

def pull_block_trade(trade_date: str, end_date: str | None = None) -> pd.DataFrame:
    """大宗交易。"""
    pro = get_pro()
    return pro.block_trade(trade_date=trade_date)

def pull_cyq_perf(trade_date: str, end_date: str | None = None) -> pd.DataFrame:
    """每日筹码及胜率汇总（全市场单日）。"""
    pro = get_pro()
    return pro.cyq_perf(trade_date=trade_date)

def pull_forecast(ann_date: str, end_date: str | None = None) -> pd.DataFrame:
    """业绩预告（按公告日增量）。"""
    pro = get_pro()
    return pro.forecast(ann_date=ann_date)

def pull_namechange(ts_code: str = "", start_date: str = "", end_date: str = "") -> pd.DataFrame:
    """股票曾用名（ST 识别）。全量重建模式。"""
    pro = get_pro()
    kwargs = {"ts_code": ts_code} if ts_code else {}
    if start_date: kwargs["start_date"] = start_date
    if end_date: kwargs["end_date"] = end_date
    return pro.namechange(**kwargs)

def pull_concept(trade_date: str = "", ts_code: str = "") -> pd.DataFrame:
    """概念板块列表。全量重建模式。"""
    pro = get_pro()
    kwargs = {}
    if trade_date: kwargs["trade_date"] = trade_date
    if ts_code: kwargs["ts_code"] = ts_code
    return pro.concept(**kwargs)
