"""池内深度数据同步（三档第二档，per-symbol）。

从 Tushare 按 ts_code 逐标的拉取池内成员的财务/筹码/股东数据。
beat 300s，时间盒 280s，SyncLock 防重叠。
"""
from __future__ import annotations
import json, logging, time
from datetime import date
from src.data_platform import db as _pdb
from .sync_lock import SyncLock

logger = logging.getLogger("data_sync.pool_data")

POOL_DATA_TYPES = {
    "income": {"api":"income","pk":["ts_code","ann_date","end_date"],
        "core":{"total_revenue":"total_revenue","revenue":"revenue","total_profit":"total_profit",
                 "n_income":"n_income","n_income_attr_p":"n_income_attr_p","basic_eps":"basic_eps",
                 "diluted_eps":"diluted_eps","rd_exp":"rd_exp","report_type":"report_type"},
        "params":{"report_type":"1"}},
    "balancesheet": {"api":"balancesheet","pk":["ts_code","ann_date","end_date"],
        "core":{"total_assets":"total_assets","total_cur_assets":"total_cur_assets",
                 "total_nca":"total_nca","total_liab":"total_liab","total_cur_liab":"total_cur_liab",
                 "total_ncl":"total_ncl","total_hldr_eqy_exc_min_int":"total_hldr_eqy_exc_min_int",
                 "money_cap":"money_cap","goodwill":"goodwill","report_type":"report_type"},
        "params":{"report_type":"1"}},
    "cashflow": {"api":"cashflow","pk":["ts_code","ann_date","end_date"],
        "core":{"n_cashflow_act":"n_cashflow_act","n_cashflow_inv_act":"n_cashflow_inv_act",
                 "n_cash_flows_fnc_act":"n_cash_flows_fnc_act","net_profit":"net_profit",
                 "c_fr_sale_sg":"c_fr_sale_sg","free_cashflow":"free_cashflow","report_type":"report_type"},
        "params":{"report_type":"1"}},
    "fina_indicator": {"api":"fina_indicator","pk":["ts_code","ann_date","end_date"],
        "core":{"eps":"eps","roe":"roe","roa":"roa","gross_margin":"gross_margin",
                 "netprofit_margin":"netprofit_margin","current_ratio":"current_ratio",
                 "quick_ratio":"quick_ratio","debt_to_assets":"debt_to_assets",
                 "assets_turn":"assets_turn","revenue_ps":"revenue_ps","bps":"bps",
                 "ocfps":"ocfps","roe_yearly":"roe_yearly","netprofit_yoy":"netprofit_yoy",
                 "revenue_yoy":"revenue_yoy"}, "params":{}},
    "cyq_chips": {"api":"cyq_chips","pk":["ts_code","trade_date","price"],
        "core":{"percent":"percent"}, "params":{}},
    "top10_holders": {"api":"top10_holders","pk":["ts_code","ann_date","end_date","holder_name"],
        "core":{"hold_amount":"hold_amount","hold_ratio":"hold_ratio",
                 "hold_float_ratio":"hold_float_ratio","hold_change":"hold_change",
                 "holder_type":"holder_type"}, "params":{}},
    "dividend": {"api":"dividend","pk":["ts_code","end_date","div_proc"],
        "core":{"ann_date":"ann_date","div_proc":"div_proc","stk_div":"stk_div",
                 "cash_div":"cash_div","cash_div_tax":"cash_div_tax",
                 "record_date":"record_date","ex_date":"ex_date","pay_date":"pay_date"},
        "params":{}},
    "pledge_stat": {"api":"pledge_stat","pk":["ts_code","end_date"],
        "core":{"pledge_count":"pledge_count","unrest_pledge":"unrest_pledge",
                 "rest_pledge":"rest_pledge","total_share":"total_share",
                 "pledge_ratio":"pledge_ratio"}, "params":{}},
    "share_float": {"api":"share_float","pk":["ts_code","float_date"],
        "core":{"ann_date":"ann_date","float_date":"float_date","float_share":"float_share",
                 "float_ratio":"float_ratio","holder_name":"holder_name","share_type":"share_type"},
        "params":{}},
    "stk_holdernumber": {"api":"stk_holdernumber","pk":["ts_code","end_date"],
        "core":{"ann_date":"ann_date","holder_num":"holder_num"}, "params":{}},
}

def _get_pool_ts_codes():
    from src.data_platform.schema import vt_to_ts
    with _pdb.get_conn() as conn:
        cur = conn.execute(
            "SELECT DISTINCT ps.symbol FROM pool_symbols ps "
            "JOIN pools p ON p.id = ps.pool_id WHERE p.category='astock'")
        return [vt_to_ts(r[0]) for r in cur.fetchall() if r[0]]

def _upsert_rows(table, pk_cols, core_map, df, ts_code):
    if df is None or df.empty: return 0
    import pandas as pd
    insert_cols = pk_cols + [c for c in core_map if c not in pk_cols]
    raw_tables = {"income","balancesheet","cashflow","fina_indicator"}
    if table in raw_tables: insert_cols = insert_cols + ["raw_json"]
    placeholders = ", ".join(["%s"]*len(insert_cols))
    conflict = ", ".join(pk_cols)
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in insert_cols if c not in pk_cols and c != "raw_json")
    upsert = (f"INSERT INTO {table} ({', '.join(insert_cols)}) VALUES ({placeholders}) "
              f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}" if updates else
              f"INSERT INTO {table} ({', '.join(insert_cols)}) VALUES ({placeholders}) "
              f"ON CONFLICT ({conflict}) DO NOTHING")
    saved = 0
    with _pdb.get_conn() as conn:
        with conn.cursor() as cur:
            for _, row in df.iterrows():
                vals = []
                for c in insert_cols:
                    if c == "raw_json":
                        vals.append(json.dumps({k: str(v) for k, v in row.items() if pd.notna(v)}, ensure_ascii=False))
                    elif c in core_map:
                        v = row.get(core_map[c])
                        try:
                            vals.append(float(v) if v is not None and _is_num(v) else (str(v) if v is not None else None))
                        except: vals.append(str(v) if v is not None else None)
                    else:
                        v = row.get(c)
                        vals.append(str(v) if v is not None else None)
                cur.execute(upsert, vals)
                saved += 1
        conn.commit()
    return saved

def _is_num(v):
    try: float(v); return True
    except: return False

def sync_pools_data(timebox_s=280):
    from src.data_platform.adapters import tushare_adapter as adapter
    lock = SyncLock("pool_data")
    try:
        return _sync_pools_data_inner(adapter, timebox_s)
    except Exception as e:
        # 观测护栏：任何未捕获异常也落 sync_log（2026-08-20 排障：任务静默死无日志）
        import traceback
        _log(0, 0, [f"FATAL {type(e).__name__}: {str(e)[:150]} :: {traceback.format_exc()[-300:]}"], False, status="error")
        return {"status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"}


def _sync_pools_data_inner(adapter, timebox_s):
    lock = SyncLock("pool_data")
    with lock:
        if not lock.acquired:
            _log(0, 0, ["skipped: 上轮仍在运行"], False, status="skipped")
            return {"status":"skipped","reason":"上轮仍在运行"}
        ts_codes = _get_pool_ts_codes()
        if not ts_codes: return {"status":"idle","reason":"无池标的"}
        pro = adapter.get_pro()
        deadline = time.time() + timebox_s
        total_saved = 0; errors = []
        for ts_code in ts_codes:
            for table, spec in POOL_DATA_TYPES.items():
                if time.time() >= deadline:
                    _log(len(ts_codes), total_saved, errors, True)
                    return {"status":"timebox","saved":total_saved,"errors":errors[:5]}
                try:
                    kwargs = {"ts_code": ts_code, **spec["params"]}
                    if table == "cyq_chips":
                        kwargs["trade_date"] = date.today().strftime("%Y%m%d")
                    df = getattr(pro, spec["api"])(**kwargs)
                    total_saved += _upsert_rows(table, spec["pk"], spec["core"], df, ts_code)
                except Exception as e:
                    errors.append(f"{ts_code}/{table}: {type(e).__name__}: {str(e)[:40]}")
                    logger.warning("池数据 %s/%s: %s", ts_code, table, e)
        _log(len(ts_codes), total_saved, errors, False)
        return {"status":"done" if not errors else "partial",
                "symbols":len(ts_codes),"saved":total_saved,"errors":errors[:5]}

def _log(symbols, saved, errors, timeout, status=None):
    try:
        with _pdb.get_conn() as conn:
            conn.execute(
                "INSERT INTO sync_log (sync_id, ts, mode, start_date, end_date, rows_pulled, rows_saved, duration_ms, status, error, failed_dates) "
                "VALUES (%s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("pool_data", "beat", "", "", symbols, saved, 0,
                 status or ("timeout" if timeout else ("done" if not errors else "partial")),
                 "", "; ".join(errors[:3])[:400]))
            conn.commit()
    except Exception as log_e:
        logger.error("sync_log 写入失败: %s", log_e)
