"""标的筛选字段注册表（批 164a 期 1——补审 P1-6 六元组形态）。

品类词表=ASSET_CATEGORIES 单源子集（双盲 A-P0-1）。
字段六元组（b 编译器/c DSL 共同消费的单源）：
  name/label/source('column'现库列|'cache'预计算缓存表|'expr'内联SQL)/type('num'|'bool')/
  nullable(True=库可为 NULL——builder 置灰依据；NULL 行在比较过滤时剔除)/group(下拉分组)
NULL 立法（164b 前钉死）：数值比较 op 遇 NULL 行=剔除不参与匹配；percentile 母数=剔 NULL 后计数。
"""

from __future__ import annotations

from ..quant_common.markets import ASSET_CATEGORIES  # 品类单源（层0←层1-2 合法向）

# group 常量（下拉分组——三品类统一词表）
GROUPS = ("valuation", "size", "quality", "tech", "other")

FIELDS: dict[str, dict[str, dict]] = {
    "stock": {
        "pe_ttm":    {"label": "PE(TTM)", "source": "column", "col": "d.pe_ttm",
                      "type": "num", "nullable": True, "group": "valuation"},
        "circ_mv":   {"label": "流通市值(万)", "source": "column", "col": "d.circ_mv",
                      "type": "num", "nullable": True, "group": "size"},
        "dv_ratio":  {"label": "股息率%", "source": "column", "col": "d.dv_ratio",
                      "type": "num", "nullable": True, "group": "quality"},
        "ps":        {"label": "市销率", "source": "column", "col": "d.ps",
                      "type": "num", "nullable": True, "group": "valuation"},
        "st_filter": {"label": "排除ST/退市", "source": "expr",
                      "expr": "(a.name NOT LIKE '%%ST%%' AND a.name NOT LIKE '%%退%%')",
                      "type": "bool", "nullable": False, "group": "other"},   # analysis.py:104 先例
        "list_days": {"label": "上市天数", "source": "expr",
                      "expr": "(CURRENT_DATE - a.list_date::date)",
                      "type": "num", "nullable": False, "group": "other"},
        "momentum_20": {"label": "月线动量(20日)", "source": "cache", "factor": "momentum_20",
                        "type": "num", "nullable": True, "group": "tech"},   # 周期谱系：周5/月20/季60/半年120/年250
        "momentum_5":   {"label": "周线动量(5日)", "source": "cache", "factor": "momentum_5",
                        "type": "num", "nullable": True, "group": "tech"},
        "momentum_60":  {"label": "季线动量(60日)", "source": "cache", "factor": "momentum_60",
                        "type": "num", "nullable": True, "group": "tech"},
        "momentum_120": {"label": "半年线动量(120日)", "source": "cache", "factor": "momentum_120",
                        "type": "num", "nullable": True, "group": "tech"},
        "momentum_250": {"label": "年线动量(250日)", "source": "cache", "factor": "momentum_250",
                        "type": "num", "nullable": True, "group": "tech"},
        "price_vs_ma250": {"label": "年线乖离%", "source": "cache", "factor": "price_vs_ma250",
                            "type": "num", "nullable": True, "group": "tech"},
    },
    "convertible": {
        "remaining_years": {"label": "剩余年限(年)", "source": "expr",
                             "expr": "(b.maturity_date::date - CURRENT_DATE)",
                             "type": "num", "nullable": False, "group": "other"},
        "coupon_rate":     {"label": "票面利率%", "source": "column", "col": "b.coupon_rate",
                             "type": "num", "nullable": True, "group": "quality"},
    },
    "listed_fund": {},   # 期 1 待核非空率后定（B-P0-2 诚实降级）
}


def fields_for(category: str) -> dict:
    """品类字段集只读视图。"""
    if category not in FIELDS:
        raise ValueError(f"unknown category: {category}")
    return FIELDS[category]
