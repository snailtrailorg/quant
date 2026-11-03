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
        "pe":        {"label": "PE", "source": "column", "col": "d.pe",
                      "type": "num", "nullable": True, "group": "valuation"},
        "pb":        {"label": "PB", "source": "column", "col": "d.pb",
                      "type": "num", "nullable": True, "group": "valuation"},
        "total_mv":  {"label": "总市值(万)", "source": "column", "col": "d.total_mv",
                      "type": "num", "nullable": True, "group": "size"},
        "turnover":  {"label": "换手率%", "source": "column", "col": "d.turnover_rate",
                      "type": "num", "nullable": True, "group": "other"},
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
        # 转股价值 = 100 * 正股价 / 转股价（评审 P0：原式取了倒数，实测双低值 294 荒谬）
        "double_low":  {"label": "双低值", "source": "expr",
                        "expr": "(cd.close + (cd.close - 100.0 * sd.close / NULLIF(b.conv_price, 0)) "
                                "/ NULLIF(100.0 * sd.close / NULLIF(b.conv_price, 0), 0) * 100)",
                        "type": "num", "nullable": True, "group": "valuation"},
        "premium_pct": {"label": "转股溢价率%", "source": "expr",
                        "expr": "((cd.close - 100.0 * sd.close / NULLIF(b.conv_price, 0)) "
                                "/ NULLIF(100.0 * sd.close / NULLIF(b.conv_price, 0), 0) * 100)",
                        "type": "num", "nullable": True, "group": "valuation"},
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


def preset_list(category: str, uid: int) -> list[dict]:
    import json
    from .db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, name, category, source, definition, created_by, updated_at "
            "FROM screen_preset WHERE category = %s "
            "AND (created_by IS NULL OR created_by = %s) "
            "ORDER BY updated_at DESC", (category, uid))
        rows = cur.fetchall()
    out = []
    for r in rows:
        d = r[4] if isinstance(r[4], dict) else json.loads(r[4])
        skipped = []
        flds = FIELDS.get(r[2], {})
        if "rows" in d:
            skipped = [row.get("field") for row in d["rows"] if row.get("field") not in flds]
            d["rows"] = [row for row in d["rows"] if row.get("field") in flds]
        out.append({"id": r[0], "name": r[1], "category": r[2], "source": r[3],
                    "definition": d, "created_by": r[5], "updated_at": str(r[6]),
                    "skipped_fields": skipped})
    return out


def preset_create(name: str, category: str, source: str, definition: dict, uid: int) -> int:
    import json
    from .db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO screen_preset (name, category, source, definition, created_by) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (name, category, source, json.dumps(definition), uid))
        conn.commit()
        return cur.fetchone()[0]


def preset_get(pid: int) -> tuple | None:
    from .db import get_conn
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, category, source, created_by FROM screen_preset WHERE id = %s", (pid,))
        return cur.fetchone()


def preset_update(pid: int, name: str, category: str, source: str, definition: dict) -> None:
    import json
    from .db import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE screen_preset SET name=%s, category=%s, source=%s, definition=%s, updated_at=now() "
            "WHERE id=%s", (name, category, source, json.dumps(definition), pid))
        conn.commit()


def preset_delete(pid: int) -> None:
    from .db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM screen_preset WHERE id=%s", (pid,))
        conn.commit()


def run_query(category: str, fields: list, where: str, params: list, limit: int,
              null_fields: list, order_by: str | None = None) -> dict:
    from .db import get_conn
    base = build_base_from(category, fields or None)
    order = order_by or "ts_code"
    q = f"SELECT * FROM ({base}) q{where} ORDER BY {order} LIMIT {int(limit)}"
    with get_conn() as conn:
        cur = conn.execute(q, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        if null_fields:
            # 被条件剔除的 NULL 行 =（满足其余条件 ∩ 引用字段为空）——须带同一 where（评审 P1）
            null_checks = " OR ".join(f + " IS NULL" for f in null_fields)
            where_wo_nulls = where if not where else where + " AND (" + null_checks + ")"
            cur = conn.execute(f"SELECT count(*) FROM ({base}) q{where_wo_nulls}", params)
            nulls = cur.fetchone()[0]
        else:
            nulls = 0
    return {"rows": rows, "nulls_excluded": nulls, "count": len(rows)}


# 缓存 join：按 category+factor 各取自己的 max(trade_date)（全局 max 会串品类/因子——评审 P1）
_CACHE_JOIN = (" LEFT JOIN screen_factor_cache {alias} ON {alias}.ts_code = {ts_col} "
               "AND {alias}.category = '{category}' AND {alias}.factor = '{factor}' "
               "AND {alias}.trade_date = (SELECT max(trade_date) FROM screen_factor_cache "
               "WHERE category = '{category}' AND factor = '{factor}')")

# 恒带注册表字段（衍生展示列如双低/溢价——表达式单源仍取注册表）
_DISPLAY_FIELDS = {"convertible": ["double_low", "premium_pct"],
                   "stock": ["momentum_20", "price_vs_ma250"]}

# 展示列（结果表恒带——评审 P0：只查条件字段导致名称/价格列全空）
_DISPLAY = {
    "stock": {"name": "a.name", "close": "d.close", "pe": "d.pe", "pe_ttm": "d.pe_ttm",
              "pb": "d.pb", "turnover": "d.turnover_rate", "turnover_rate": "d.turnover_rate",
              "total_mv": "d.total_mv",
              "circ_mv": "d.circ_mv", "dv_ratio": "d.dv_ratio", "ps": "d.ps"},
    "convertible": {"name": "b.bond_short_name", "close": "cd.close", "bond_close": "cd.close",
                    "stk_code": "b.stk_code", "stk_name": "b.stk_short_name", "stk_close": "sd.close",
                    "conv_price": "b.conv_price", "maturity_date": "b.maturity_date",
                    "coupon_rate": "b.coupon_rate"},
    "listed_fund": {"name": "lf.name", "fund_scale": "lf.fund_scale",
                    "management_fee": "lf.management_fee", "fund_type": "lf.fund_type",
                    "tracking_error": "lf.tracking_error",
                    "invest_type": "lf.invest_type", "management": "lf.management"},
}

_BASE = {
    "stock": {
        "from": "FROM daily_basic d LEFT JOIN asset_static_info a ON a.ts_code = d.ts_code",
        "ts_col": "d.ts_code", "latest": "d.trade_date = (SELECT max(trade_date) FROM daily_basic)",
        "order": "d.total_mv DESC NULLS LAST",
    },
    "convertible": {
        # 时区双转：ts 存 UTC（上海零点=前一日 16:00 UTC）——date_trunc 出 naive 后必须再转回 timestamptz
        "from": ("FROM cb_basic_info b "
                 "LEFT JOIN (SELECT symbol, close, ts FROM bar_1d WHERE freq='1D' "
                 "AND ts >= (date_trunc('day', (SELECT max(ts) FROM bar_1d) AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai') "
                 "AND ts < (date_trunc('day', (SELECT max(ts) FROM bar_1d) AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai') + interval '1 day') cd "
                 "ON cd.symbol = replace(replace(b.ts_code, '.SZ', '.SZSE'), '.SH', '.SHSE') "
                 "LEFT JOIN (SELECT ts_code, close FROM daily_basic "
                 "WHERE trade_date = (SELECT max(trade_date) FROM daily_basic)) sd ON b.stk_code = sd.ts_code"),
        "ts_col": "b.ts_code", "latest": None, "order": "double_low NULLS LAST",
    },
    "listed_fund": {
        "from": "FROM listed_fund_basic lf",
        "ts_col": "lf.ts_code", "latest": None, "order": "lf.ts_code",
    },
}


def build_base_from(category: str, fields: list[str] | None = None) -> str:
    """品类基础查询段（SELECT+FROM+JOIN）——b 编译器与 c 求值器同引单源（164b 件3/B-P0）。

    fields=引用字段集（决定 cache JOIN 增补与 SELECT 列）；返回可直接拼 WHERE 的 base SQL 头。
    """
    base = _BASE[category]
    flds = fields_for(category)
    joins = base["from"]
    # 输出列：展示列打底 → 条件字段覆盖（同名字段去重——评审 P0：同字段两条件会重复 JOIN/重名列）
    out = dict(_DISPLAY[category])
    cache_joined = set()
    ordered = list(_DISPLAY_FIELDS.get(category, [])) + list(dict.fromkeys(fields or []))
    for f in ordered:
        spec = flds.get(f)
        if spec is None:
            continue
        if spec["source"] == "cache":
            factor = spec["factor"]
            if factor not in cache_joined:
                alias = f"f_{factor}"
                joins += _CACHE_JOIN.format(alias=alias, ts_col=base["ts_col"],
                                            factor=factor, category=category)
                cache_joined.add(factor)
            out[f] = f"f_{spec['factor']}.value"
        elif spec["source"] == "column":
            out[f] = spec["col"]
        else:
            out[f] = spec["expr"]
    selects = [f"{base['ts_col']} AS ts_code"] + [f"{expr} AS {name}" for name, expr in out.items()]
    latest = f" WHERE {base['latest']}" if base["latest"] else ""
    return f"SELECT {', '.join(selects)} {joins}{latest}"


# ── 条件编译器（批 164b 件4）──
# 契约（任务 v3 步骤 4/5）：definition={v:1, match:'all'|'any', rows:[{field,op,value}]}
# 产 where 作用于 build_base_from 的**外层子查询**（PG 同层 WHERE 不可引 SELECT 别名——实证）。
# 注入面零拼接：field 恒取注册表字符串（查表键）；op 走白名单映射；value 全参数绑定。

_OPS = {
    "=": "= %s", "!=": "!= %s", ">": "> %s", ">=": ">= %s", "<": "< %s", "<=": "<= %s",
}
_PERCENTILE = {"percentile_top", "percentile_bottom"}


def compile_conditions(category: str, definition: dict) -> tuple[str, list, list[str]]:
    """编译 builder 式 definition → (where_sql, params, null_field_notes)。

    返回 where 片段（含外层子查询包裹头）、绑定参数序、NULL 语义注记（供响应 skipped/nulls 提示）。
    错误：ValueError(field 未知/op×类型错配/percentile 边界/bool 非 true)——路由层转 400/422。
    """
    flds = fields_for(category)
    rows = definition.get("rows", [])
    # 批 165 用户裁定：恒 AND（match 键忽略——存量 any 方案宽容降级为 AND；
    # OR/括号需求走 DSL 页签，业界 builder 无 any 先例）
    parts, params, notes = [], [], []
    for row in rows:
        f, op, val = row.get("field"), row.get("op"), row.get("value")
        spec = flds.get(f)
        if spec is None:
            raise ValueError(f"unknown field: {f}")
        if op in _PERCENTILE:
            if spec["type"] != "num":
                raise ValueError(f"percentile 仅数值字段: {f}")
            if not (isinstance(val, (int, float)) and 0 <= val <= 100):
                raise ValueError("percentile value∈[0,100]")
            # 母数=品类当日全集∩剔该字段 NULL（与 164c rank 母数同口径）；percentile_cont 标量子查询
            direction = ">=" if op == "percentile_top" else "<="
            sub = (f"(SELECT percentile_cont({val / 100}) WITHIN GROUP (ORDER BY q.{f}) "
                   f"FROM ({build_base_from(category, [f])}) q WHERE q.{f} IS NOT NULL)")
            parts.append(f"{f} IS NOT NULL AND {f} {direction} {sub}")
            continue
        if op not in _OPS:
            raise ValueError(f"unknown op: {op}")
        if spec["type"] == "bool":
            if op != "=" or val is not True:
                raise ValueError(f"bool 字段仅支持 = true（极性立法）: {f}")
            parts.append(f"{f} = true")   # 正向谓词直取（st_filter=false=只留ST——陷阱封死）
            continue
        if spec["type"] != "num":
            raise ValueError(f"op 仅数值: {f}")
        if not isinstance(val, (int, float)):
            raise ValueError(f"value 须数值: {f}")
        frag = _OPS[op]
        col = f   # 外层子查询别名=字段名
        parts.append(f"{col} IS NOT NULL AND {col} {frag}")
        params.append(val)
        if spec.get("nullable"):
            notes.append(f)
    where = (" WHERE " + " AND ".join(parts)) if parts else ""
    return where, params, notes
