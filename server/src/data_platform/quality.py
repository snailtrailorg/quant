"""批 62b（M7）：shadow 采样对账引擎+口径白名单+SM 对账（A03 §十五/A04 §十）。

**自洽边界立法（A-P1-3，方案 §前言）**：主源=PG 已存 vs 备源=Tushare 现拉=同源双时刻——
检出边界=入库链改数/降级写入/部分写/后续覆写；**源侧系统性错=双拉一致漏报**（「容忍一致地错」
承受面）。报表 KPI 固定标注「同源自洽（入库链对账）——不覆盖源侧系统误差」。

治理三条（A03 §15.1 v3）：白名单版本化（whitelist_semantic.json）+验证义务（system_config
whitelist_verify:{id}，90 天 beat 告警）+命中率双向监控（≥99% 静音器防线/0 命中死条目）。

预算（A-P1-6/B-P1-3 终版）：单位=API 调用次数/月；引擎 fetch 包 rate_limit_context
（D1 立法限速在 engine 侧）+record_usage 带 api_name 前缀 ``shadow:``——预算=
SUM(calls) WHERE api_name LIKE 'shadow:%' 当月（常规同步流量不进闸门）；sample 地板≥3，
破地板=跳过执行+升级告警（不静默砍零）。
"""
from __future__ import annotations

import json
import logging
import random
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("quality")

# 层 1 → alert_notify：分层豁免表内（EXEMPT_UPWARD 先例——日志/告警面横切）；模块级便于测试 patch
from src.alert_notify.notify import safe_notify

_WHITELIST_PATH = Path(__file__).parent / "whitelist_semantic.json"
SAMPLE_FLOOR = 3            # A-P1-6：采样地板——破地板跳过执行而非静默缩量
ALERT_REAL_DIFF_N = 10      # A-P1-2：真 diff（非白名单命中）行数阈值→critical 告警
DEFAULT_TOLERANCE = {"open": {"abs": 0.01, "rel": 1e-4}, "high": {"abs": 0.01, "rel": 1e-4},
                     "low": {"abs": 0.01, "rel": 1e-4}, "close": {"abs": 0.01, "rel": 1e-4},
                     "volume": {"rel": 1e-3}, "amount": {"rel": 1e-3}, "adj_factor": {"rel": 1e-4}}
DIFF_FIELDS = ("open", "high", "low", "close", "volume", "amount", "adj_factor")
SAMPLE_WINDOW_DAYS = 5      # v3.1：采样窗 N=5 交易日（锚 T-1 及以前——Tushare 日线时点不稳）


def load_whitelist() -> list[dict]:
    """口径白名单（版本化真源=代码仓文件；坏文件=空表 fail-safe 不阻断引擎）。"""
    try:
        with open(_WHITELIST_PATH) as f:
            data = json.load(f)
        return [e for e in data.get("entries", []) if e.get("生效", True)]
    except Exception as e:
        logger.warning("白名单加载失败（按空表处理）: %s", e)
        return []


def _match_entry(entries: list[dict], field: str, main_val, backup_val) -> dict | None:
    """白名单匹配（B-P1-4 机器 schema）：match.field 单字段或 match.fields 多字段；
    rule 应用在 _apply_rule。返回命中条目（None=真 diff）。"""
    for e in entries:
        m = e.get("match", {})
        if field not in ([m["field"]] if m.get("field") else m.get("fields", [])):
            continue
        rule = e.get("rule", {})
        t = rule.get("type")
        if t == "skip_field":
            return e
        if t == "skip_null_main" and main_val is None:
            return e
        if t == "log_only":
            return e   # 记白名单命中不进告警计数（人工报表区分停牌/真缺）
        if t == "tolerance_rel" and main_val not in (None, 0) and backup_val is not None:
            try:
                if abs(float(backup_val) - float(main_val)) / abs(float(main_val)) <= float(rule.get("value", 0)):
                    return e
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        if t == "tolerance_abs" and main_val is not None and backup_val is not None:
            try:
                if abs(float(backup_val) - float(main_val)) <= float(rule.get("value", 0)):
                    return e
            except (TypeError, ValueError):
                continue
    return None


def _within_tolerance(field: str, main_val, backup_val, tolerance: dict | None) -> bool:
    """引擎级 per-field 容差（白名单之外的默认容差——policy.tolerance 或 DEFAULT）。"""
    tol = (tolerance or DEFAULT_TOLERANCE).get(field) or DEFAULT_TOLERANCE.get(field)
    if main_val is None or backup_val is None:
        return main_val == backup_val   # 单侧缺失=差异（进白名单判定——如 adj NULL 降级条目）
    if tol is None:
        return True
    try:
        mv, bv = float(main_val), float(backup_val)
    except (TypeError, ValueError):
        return True
    diff = abs(bv - mv)
    if "abs" in tol and diff <= tol["abs"]:
        return True
    if "rel" in tol and mv != 0:
        return diff / abs(mv) <= tol["rel"]
    return False


def diff_rows(main: list[dict], backup: list[dict], tolerance: dict | None = None) -> list[dict]:
    """纯函数：主备行集 → diff 行列表（白名单归一后再比对——A03 §15.1「先归一再 diff」）。

    行键=(symbol, trade_date)；主缺=__missing__/主多=__extra__（A-P2-5 伪字段）。
    返回 [{symbol, trade_date, field, main_val, backup_val, whitelist_hit}]——
    whitelist_hit=None 的行=真 diff（告警计数只数这些）。
    """
    entries = load_whitelist()
    bidx = {(b["symbol"], str(b["trade_date"])): b for b in backup}
    midx = {(m["symbol"], str(m["trade_date"])): m for m in main}
    out = []
    for key, m in midx.items():
        b = bidx.get(key)
        if b is None:
            hit = _match_entry(entries, "__missing__", None, None)
            out.append({"symbol": key[0], "trade_date": key[1], "field": "__missing__",
                        "main_val": None, "backup_val": None,
                        "whitelist_hit": hit["id"] if hit else None})
            continue
        for f in DIFF_FIELDS:
            mv, bv = m.get(f), b.get(f)
            if mv is None and bv is None:
                continue
            if _within_tolerance(f, mv, bv, tolerance):
                continue
            hit = _match_entry(entries, f, mv, bv)
            out.append({"symbol": key[0], "trade_date": key[1], "field": f,
                        "main_val": mv, "backup_val": bv,
                        "whitelist_hit": hit["id"] if hit else None})
    for key in bidx:
        if key not in midx:
            out.append({"symbol": key[0], "trade_date": key[1], "field": "__extra__",
                        "main_val": None, "backup_val": None, "whitelist_hit": None})
    return out


def _month_usage(conn) -> int:
    """当月 shadow 自身调用量（v3.1 裁定：api_name 前缀 shadow:——常规同步流量不进闸门）。"""
    cur = conn.execute(
        "SELECT coalesce(sum(calls), 0) FROM data_source_usage "
        "WHERE api_name LIKE 'shadow:%%' AND ts >= date_trunc('month', now())")
    return int(cur.fetchone()[0])


def _sample_symbols(conn, kind: str, n: int) -> list[str]:
    """采样标的（seed=当日——可重复；bar 表 distinct symbol 近窗有数据者）。"""
    table = {"bar_daily": "bar_1D", "bar_minute": "bar_1min"}.get(kind)
    if not table:
        return []
    cur = conn.execute(
        f"SELECT DISTINCT symbol FROM {table} WHERE ts >= now() - interval '30 days' LIMIT 200")
    syms = [r[0] for r in cur.fetchall()]
    rng = random.Random(date.today().isoformat())
    return rng.sample(syms, min(n, len(syms))) if syms else []


def _fetch_backup_daily(days: list[date]) -> dict[str, dict]:
    """备源=Tushare 逐交易日全市场批拉→过滤为 {(symbol, date): row}（B-P1-2 裁定：
    fetch(bar_daily) 是按日全市场批无 per-symbol）。每次调用包 rate_limit_context
    +record_usage('shadow:daily')（B-P1-3/v3.1）。"""
    from src.data_platform.data_source import get_data_source, TushareDataSource
    from src.data_platform.rate_limit import rate_limit_context
    out: dict[str, dict] = {}
    # 分层：data_platform(层1) 禁 import data_sync(层3)——ds=adapter 同一 DataSource 实例
    # （rate_limit_context 消费其限速/熔断接口；_get_rate_ds 是 data_sync 侧包装，此处等价直取）
    adapter = get_data_source("tushare") or TushareDataSource()
    ds = adapter
    for d in days:
        td = d.strftime("%Y%m%d")
        for kind in ("astock", "etf"):   # bar_1D 混 stock/etf 两类（B-P1-2：按日全市场批分派 sub_kind）
            with rate_limit_context(ds, "shadow:daily", min_interval=0.35):
                try:
                    df = adapter.pull_daily_batch(td, kind)
                    ds.record_usage(api_calls=1, api_name="shadow:daily", provider="tushare")
                except Exception as e:
                    logger.warning("备源拉取失败 %s %s: %s", d, kind, e)
                    continue
            for r in (df.to_dict("records") if df is not None and not df.empty else []):
                sym = r.get("ts_code", "")
                out[(sym, str(d))] = {"symbol": sym, "trade_date": str(d),
                                      "open": r.get("open"), "high": r.get("high"),
                                      "low": r.get("low"), "close": r.get("close"),
                                      "volume": (r.get("vol") or 0) * 100 if r.get("vol") is not None else None,
                                      "amount": (r.get("amount") or 0) * 1000 if r.get("amount") is not None else None,
                                      "adj_factor": r.get("adj_factor")}
    return out


def run_shadow_check(kind: str = "bar_daily") -> dict:
    """采样对账主入口（beat 16:05 交易日+手动端点）。返回统计摘要（报表/测试消费）。"""
    from src.data_platform.db import get_conn
    result = {"kind": kind, "ran": False, "sampled": 0, "diff_rows": 0, "real_diff": 0,
              "budget_used": 0, "skipped_reason": None}
    with get_conn() as conn:
        cur = conn.execute("SELECT backup_source, sample_n, tolerance, budget_calls_month, enabled "
                           "FROM shadow_policy WHERE kind=%s", (kind,))
        pol = cur.fetchone()
        if not pol or not pol[4]:
            result["skipped_reason"] = "policy 不存在或 disabled（分钟级=采购 gate 空档）"
            return result
        _, sample_n, tolerance, budget, _ = pol
        used = _month_usage(conn)
        result["budget_used"] = used
        if used >= budget:
            # 预算超限：降采样；地板之下=跳过+升级告警（A-P1-6——不静默砍零）
            n = max(SAMPLE_FLOOR, sample_n // 2)
            if n < SAMPLE_FLOOR or used >= budget * 2:
                safe_notify("critical", "shadow 对账预算耗尽跳过",
                            f"kind={kind} 当月 {used}/{budget} 次——人工核对预算配置或数据源用量",
                            code="quality.budget-exhausted")
                result["skipped_reason"] = f"预算 {used}/{budget}"
                return result
            sample_n = n
            safe_notify("warn", "shadow 对账预算超限降采样",
                        f"kind={kind} 当月 {used}/{budget} 次，采样降至 {sample_n}（地板 {SAMPLE_FLOOR}）",
                        code="quality.budget-throttle")
        symbols = _sample_symbols(conn, kind, sample_n)
        if not symbols:
            result["skipped_reason"] = "无采样标的（bar 表空？）"
            return result
        # 主源：PG（采样窗=T-1 起往前 N 交易日——A-P2-5：当日 Tushare 半出=狼来了）
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=SAMPLE_WINDOW_DAYS + 4)   # +4 自然日≈5 交易日
        main = []
        for sym in symbols:
            cur = conn.execute(
                "SELECT symbol, ts::date, open, high, low, close, volume, amount, adj_factor "
                "FROM bar_1D WHERE symbol=%s AND ts::date BETWEEN %s AND %s", (sym, start, end))
            for r in cur.fetchall():
                main.append({"symbol": r[0], "trade_date": str(r[1]), "open": r[2], "high": r[3],
                             "low": r[4], "close": r[5], "volume": r[6], "amount": r[7],
                             "adj_factor": r[8]})
    backup_map = _fetch_backup_daily([end - timedelta(days=i) for i in range(SAMPLE_WINDOW_DAYS + 4)])
    backup = [v for (s, d), v in backup_map.items() if s in set(symbols)]
    rows = diff_rows(main, backup, tolerance)
    real = [r for r in rows if not r["whitelist_hit"]]
    result.update(ran=True, sampled=len(symbols), diff_rows=len(rows), real_diff=len(real))
    if rows:
        with get_conn() as conn:
            for r in rows:
                conn.execute(
                    "INSERT INTO shadow_diff (kind, symbol, trade_date, field, main_val, backup_val, whitelist_hit) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (kind, r["symbol"], r["trade_date"], r["field"],
                     r["main_val"], r["backup_val"], r["whitelist_hit"]))
            conn.commit()
    if len(real) > ALERT_REAL_DIFF_N:
        safe_notify("critical", f"shadow 对账真差异 {len(real)} 行（kind={kind}）",
                    f"超容差（非白名单命中）{len(real)} 行>阈值 {ALERT_REAL_DIFF_N}——"
                    "对账报表查看明细；restate 排查指引见报表页。",
                    code="quality.real-diff")
    return result


def whitelist_stats(days: int = 30) -> list[dict]:
    """命中率双向统计（治理③）：条目命中数/该 kind 当期 diff 行数（v3 分母钉死）；
    event-driven 条目不进比率；附上次人工验证（system_config whitelist_verify:{id}）。"""
    from src.data_platform.db import get_conn
    entries = load_whitelist()
    out = []
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT count(*) FROM shadow_diff WHERE created_at >= now() - interval %s day",
                               (days,))
            total = cur.fetchone()[0]
            ver_keys = [f"whitelist_verify:{e['id']}" for e in entries]
            cur = conn.execute("SELECT key, value FROM system_config WHERE key = ANY(%s)", (ver_keys,))
            verified = dict(cur.fetchall())
            for e in entries:
                cur = conn.execute(
                    "SELECT count(*) FROM shadow_diff WHERE whitelist_hit=%s AND created_at >= now() - interval %s day",
                    (e["id"], days))
                hits = cur.fetchone()[0]
                stat = {"id": e["id"], "hits": hits, "last_verified": verified.get(f"whitelist_verify:{e['id']}"),
                        "opportunity": e.get("适用机会", "daily-expected")}
                if stat["opportunity"] == "daily-expected" and total:
                    stat["hit_rate"] = round(hits / total, 4)
                out.append(stat)
    except Exception as e:
        logger.warning("白名单统计失败: %s", e)
    return out


def sm_reconcile() -> dict:
    """批 62c：SM 对账差集（A-P1-4/B-P1-5 终版——范围钉死 category='stock'×list_status='L'，
    键归一 ts_code→vt_symbol；ETF/转债=范围外不计；退市漂移单列）。金标准源=
    system_config sm_golden_static_list（多源 static_list diff 留桩——第二源接入前单源自检）。"""
    from src.data_platform.db import get_conn
    from src.data_platform.schema import to_vt_symbol
    res = {"sm_only": [], "static_only": [], "delist_drift": [], "out_of_scope_note":
           "ETF/转债不在本差集范围（static_symbols 仅 A 股股票——范围外不计）"}
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT vt_symbol FROM security_master WHERE category='stock'")
            sm = {r[0] for r in cur.fetchall()}
            cur = conn.execute("SELECT ts_code FROM static_symbols WHERE list_status='L'")
            live_static = set()
            for r in cur.fetchall():
                try:
                    live_static.add(to_vt_symbol(r[0]))
                except Exception:
                    live_static.add(r[0])
            cur2 = conn.execute(
                "SELECT ts_code FROM static_symbols WHERE delisted=true OR list_status='D'")
            delisted = set()
            for r in cur2.fetchall():
                try:
                    delisted.add(to_vt_symbol(r[0]))
                except Exception:
                    delisted.add(r[0])
            res["sm_only"] = sorted(sm - live_static - delisted)[:200]
            res["static_only"] = sorted(live_static - sm)[:200]
            res["delist_count"] = len(delisted)
    except Exception as e:
        logger.warning("SM 对账失败: %s", e)
    return res


def whitelist_verify_check(max_age_days: int = 90) -> list[dict]:
    """验证义务执法（A-P1-7）：条目超 90 天未验证→告警；新条目（从未验证）自首次入册 90 天后计入。"""
    from src.data_platform.db import get_conn
    overdue = []
    try:
        with get_conn() as conn:
            for e in json.loads(json.dumps(load_whitelist())):   # 深拷贝隔离
                key = f"whitelist_verify:{e['id']}"
                cur = conn.execute("SELECT value FROM system_config WHERE key=%s", (key,))
                row = cur.fetchone()
                last = row[0] if row else None
                if not last:
                    continue   # 从未验证：宽限（首验义务由入册评审承担——报表页黄标提示）
                try:
                    age = (date.today() - date.fromisoformat(str(last)[:10])).days
                except ValueError:
                    continue
                if age > max_age_days:
                    overdue.append({"id": e["id"], "last_verified": last, "age": age})
    except Exception as e:
        logger.warning("验证义务检查失败: %s", e)
    if overdue:
        safe_notify("warn", f"口径白名单 {len(overdue)} 条超 90 天未验证",
                    "；".join(f"{o['id']}(上次 {o['last_verified'][:10]})" for o in overdue)
                    + "——对账报表回填验证结果（防白名单退化成告警静音器）",
                    code="quality.whitelist-stale")
    return overdue
