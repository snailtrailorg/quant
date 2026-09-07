"""历史 K 线数据源 adapter 基类 + 注册表（24 号多数据源架构）。

职责分层（组合不继承，复审 B-P1-5）：
- DataSource（data_source.py）：连接/token/限速/熔断/用量
- BaseDataAdapter（本文件）：K 线拉取 + 字段映射到统一 11 字段

加聚宽/米筐 = 实现 BaseDataAdapter 子类 + 注册 _ADAPTERS + DB 配 sync_config.provider。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class UnsupportedFeature(Exception):
    """该数据源不支持某能力（如按日全市场批量拉取）。真接批实现 per-symbol 降级，本批只抛出。"""


class BaseDataAdapter(ABC):
    """历史 K 线数据源 adapter（对齐 vnpy BaseDatafeed）。

    per-symbol 拉取是核心（所有源都实现）；按日批量是能力方法（Tushare 专属）。
    to_bar_rows 是「统一 11 字段」唯一出口——所有源拉取返回源原生 DataFrame，
    to_bar_rows 转统一 (symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)。
    """
    provider: str = ""
    # 能力矩阵（24 号）：该平台能提供的同步项 sync_id 集合。供给矩阵只列这些
    # （由 adapter 类属性派生，非 DB JSON——盲审 A-P2/B-P2 双真相源会漂移）
    capabilities: set[str] = set()

    @abstractmethod
    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "astock") -> pd.DataFrame:
        """per-symbol 日线。symbol=ts_code（如 600000.SH）；kind=astock/etf/cb（源接口路由）。"""

    @abstractmethod
    def pull_minute(self, symbol: str, freq: str, start: str, end: str) -> pd.DataFrame:
        """per-symbol 分钟线。"""

    @abstractmethod
    def to_bar_rows(self, df: pd.DataFrame, freq: str, adj_map: dict | None = None) -> list[tuple]:
        """字段映射 → 统一 11 字段。

        - source = self.provider
        - adj_map 非 None = 批量路径（pro.daily 无 adj_factor 列，因子来自外部 adj_map）；
          adj_map is None = per-symbol 路径（因子来自 df["adj_factor"]）
        - freq 含 "min" 读 trade_time 列，否则 trade_date 列
        """

    def pull_daily_batch(self, trade_date: str, kind: str) -> pd.DataFrame:
        """按日全市场批量（Tushare pro.daily/fund_daily 专属）。默认 UnsupportedFeature。"""
        raise UnsupportedFeature(f"{self.provider} 不支持按日批量")

    def pull_adj_factor(self, symbol=None, trade_date=None, start=None, end=None) -> pd.DataFrame:
        """复权因子（Tushare adj_factor 专属）。默认空（因子缺省 NULL）。

        symbol + start/end = 单标的区间因子；trade_date = 全市场单日因子。
        """
        return pd.DataFrame()

    # —— 归一化（带默认实现，Tushare 直通，聚宽/米筐覆写，24 号 §2.1）——
    def to_source_symbol(self, symbol: str) -> str:
        """内部 ts_code（600000.SH）→ 源格式（聚宽 600000.XSHG）。默认直通。"""
        return symbol

    def from_source_symbol(self, source_symbol: str) -> str:
        """源格式 → 内部 ts_code（to_bar_rows 归一到 vt_symbol 的输入）。默认直通。"""
        return source_symbol

    def to_source_freq(self, freq: str) -> str:
        """内部 freq（'1min'）→ 源格式（聚宽 '1m'）。默认直通。"""
        return freq

    def to_source_adj(self, adj: str | None) -> str | None:
        """语义复权（'pre'/'post'/None）→ 源格式（Tushare 'qfq'/'hfq'/None）。默认直通。"""
        return adj


# ——— 注册表 ———
_ADAPTERS: dict[str, type[BaseDataAdapter]] = {}


def register_adapter(cls: type[BaseDataAdapter]) -> type[BaseDataAdapter]:
    """注册 adapter（装饰器）。"""
    _ADAPTERS[cls.provider] = cls
    return cls


def get_adapter(provider: str) -> BaseDataAdapter:
    """从注册表实例化 adapter。provider 未注册抛 ValueError。"""
    cls = _ADAPTERS.get(provider)
    if not cls:
        raise ValueError(f"未注册的数据源 adapter: {provider}")
    return cls()


@register_adapter
class TushareAdapter(BaseDataAdapter):
    """Tushare 历史 K 线 adapter（mapper 三合一 + 统一走 DataSource，24 号）。

    盲审 A-P1/B-P1 修复：缓存 self._ds，pull_daily/pull_minute/pull_adj_factor/pull_daily_batch
    全走 self.get_client()（DataSource DB 解密 token + 限速/熔断/用量），废弃模块级 get_pro
    （.env token）——消除双 pro 实例/token 源不一致。
    """

    provider = "tushare"
    capabilities = {"astock_daily", "etf_daily", "cb_daily", "astock_minute", "astock_minute_5min"}

    def __init__(self):
        from src.data_platform.data_source import get_data_source, TushareDataSource
        self._ds = get_data_source("tushare") or TushareDataSource()
        self._pro = None

    def get_client(self):
        """Tushare pro 客户端（进程级缓存，避免每次调用重建 pro/读 DB 解密——盲审 A-P2/B-P2）。"""
        if self._pro is None:
            self._pro = self._ds.get_client()
        return self._pro

    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "astock") -> pd.DataFrame:
        """per-symbol 日线。kind 路由到 daily/fund_daily/cb_daily（盲审 A-P1-2：pro_bar 默认
        asset='E' 股票，对 ETF/转债返回空——原 _get_pro_api 按 kind 路由三条 API）。"""
        from datetime import date as _date
        pro = self.get_client()
        end = end or _date.today().strftime("%Y%m%d")
        if kind == "etf":
            df = pro.fund_daily(ts_code=symbol, start_date=start, end_date=end)
        elif kind == "cb":
            df = pro.cb_daily(ts_code=symbol, start_date=start, end_date=end)
        else:
            try:
                df = pro.pro_bar(ts_code=symbol, freq="D", start_date=start, end_date=end, adj=adj)
            except Exception:
                df = pro.daily(ts_code=symbol, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()
        if "adj_factor" not in df.columns:
            df["adj_factor"] = None
        return df

    def pull_minute(self, symbol: str, freq: str, start: str, end: str) -> pd.DataFrame:
        from datetime import date as _date
        pro = self.get_client()
        end = end or (_date.today().strftime("%Y%m%d") + " 15:00:00")
        df = pro.stk_mins(ts_code=symbol, freq=freq, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()
        df["adj_factor"] = None
        df["trade_date"] = df["trade_time"].str[:10].str.replace("-", "")
        return df

    def to_bar_rows(self, df: pd.DataFrame, freq: str, adj_map: dict | None = None) -> list[tuple]:
        """三合一 mapper：原 _daily_to_rows（批量，adj_map）+ to_save_rows（日）+ to_save_rows_min（分钟）。"""
        from src.data_platform.schema import to_vt_symbol
        from src.data_platform.adapters.tushare_adapter import _safe_float
        rows = []
        for _, row in df.iterrows():
            ts_code = row.get("ts_code", "")
            vt_sym = to_vt_symbol(ts_code)
            # ts：分钟读 trade_time，日线读 trade_date
            if "min" in freq:
                ts = pd.Timestamp(row["trade_time"]).to_pydatetime()
            else:
                ts = pd.Timestamp(row["trade_date"]).to_pydatetime()
            # adj：批量路径（adj_map 非 None）用 adj_map；per-symbol（None）用 df["adj_factor"]
            if adj_map is not None:
                adj_raw = adj_map.get(ts_code)
                adj_val = float(adj_raw) if adj_raw is not None and pd.notna(adj_raw) else None
            else:
                adj_val = _safe_float(row.get("adj_factor")) if row.get("adj_factor") and pd.notna(row.get("adj_factor")) else None
            rows.append((
                vt_sym, freq, ts,
                _safe_float(row["open"]), _safe_float(row["high"]), _safe_float(row["low"]),
                _safe_float(row["close"]),
                _safe_float(row.get("vol", 0)), _safe_float(row.get("amount", 0)),
                adj_val, self.provider,
            ))
        return rows

    def pull_daily_batch(self, trade_date: str, kind: str) -> pd.DataFrame:
        pro = self.get_client()
        if kind == "astock":
            return pro.daily(trade_date=trade_date)
        if kind == "etf":
            return pro.fund_daily(trade_date=trade_date)
        raise UnsupportedFeature(f"tushare 不支持 kind={kind} 按日批量")

    def pull_adj_factor(self, symbol=None, trade_date=None, start=None, end=None) -> pd.DataFrame:
        """复权因子。保留 None（接口不可用）与空 df（无数据）区分——backfill_adj_factor 靠 None 判 degraded。"""
        from src.data_platform.adapters.tushare_adapter import _adj_degraded_alert
        pro = self.get_client()
        try:
            if symbol:
                df = pro.adj_factor(ts_code=symbol, start_date=start or "", end_date=end or "")
            else:
                df = pro.adj_factor(trade_date=trade_date)
            if df is None or df.empty:
                return pd.DataFrame()
            return df[["ts_code", "trade_date", "adj_factor"]]
        except Exception as e:
            _adj_degraded_alert(e)
            return None


@register_adapter
class JoinQuantAdapter(BaseDataAdapter):
    """聚宽 adapter（stub，证明接口能接；真接需装 jqdatasdk + 实现字段映射）。

    真接要点（记入注释，24 号 §8.2）：
    - 字段映射：聚宽 volume(股)/money(元)，fq='pre' 前复权 → 须 pin fq=None 对齐「未复权价+adj_factor」契约
    - symbol 归一化：聚宽 000001.XSHE ↔ 内部 ts_code 000001.SZ（§4.4 留真接批）
    """
    provider = "joinquant"

    def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
        raise NotImplementedError("聚宽 adapter stub：真接需 jqdatasdk")

    def pull_minute(self, symbol, freq, start, end):
        raise NotImplementedError("聚宽 adapter stub：真接需 jqdatasdk")

    def to_bar_rows(self, df, freq, adj_map=None):
        raise NotImplementedError("聚宽 adapter stub：真接需 jqdatasdk")


@register_adapter
class RiceQuantAdapter(BaseDataAdapter):
    """米筐 adapter（stub；真接需 rqdatac + 字段映射，total_turnover 元）。"""
    provider = "ricequant"

    def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
        raise NotImplementedError("米筐 adapter stub：真接需 rqdatac")

    def pull_minute(self, symbol, freq, start, end):
        raise NotImplementedError("米筐 adapter stub：真接需 rqdatac")

    def to_bar_rows(self, df, freq, adj_map=None):
        raise NotImplementedError("米筐 adapter stub：真接需 rqdatac")
