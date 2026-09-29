"""SM 对账差集（security_master vs static_symbols）——批 62c。

批 79（2026-09-29）从批 62b shadow 对账引擎中剥离保留：shadow 行情主备对账（主源 bar_1D
vs 备源 Tushare 现拉，底层同源 pro.daily）已整体删除——同源自检只能检出「入库链改数/降级写入」
类加工 bug，抓不了源侧系统性错（一致地错），价值天花板低且会假绿。SM 对账是两张表互对
（标的清单差集），不涉行情同源问题，故保留。删除清单见 flow/任务/批79-删除shadow对账.md。
"""
import logging

logger = logging.getLogger("quality")


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
