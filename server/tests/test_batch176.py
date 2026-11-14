"""批 176 守门：派生表/对账差异单口径归一到 vt。"""
import re

from src.data_platform.screen import build_base_from
from src.scheduler.tasks import _POSITION_DIFF_SQL, _cache_rows


def test_cache_rows_dual_write():
    """因子缓存行：vt 做键、ts_code 过渡双写（contract 后删 ts）。"""
    rows = _cache_rows("momentum_20", "2026-11-13", [("600000.XSHG", 0.1), ("000001.XSHE", -0.2)])
    assert [r[2] for r in rows] == ["600000.XSHG", "000001.XSHE"]     # vt_symbol
    assert [r[1] for r in rows] == ["600000.SH", "000001.SZ"]         # ts_code（双写）
    assert all(r[0] == "stock" and r[3] == "momentum_20" for r in rows)


def test_cache_join_uses_vt_symbol():
    """选股基表 join 因子缓存必须按 vt（不再按 ts_code）。"""
    sql = build_base_from("stock", ["momentum_20"])
    assert "f_momentum_20.vt_symbol = d.vt_symbol" in sql
    assert "f_momentum_20.ts_code" not in sql


def test_position_diff_sql_stores_canonical():
    """对账比对：比对键仍裸码（兼容历史），落库键取 canonical（快照侧/主档兜底）。"""
    assert "min(symbol) AS sym_vt" in _POSITION_DIFF_SQL
    assert "sm.vt_symbol" in _POSITION_DIFF_SQL
    assert "security_master" in _POSITION_DIFF_SQL


def test_reconcile_issue_has_no_bare_code_rows():
    """真库守门：差异单不得再出现 6 位裸码（account#N 除外）。"""
    from src.data_platform.db import get_conn
    with get_conn() as c:
        n = c.execute("SELECT count(*) FROM reconcile_issue WHERE symbol ~ '^[0-9]{6}$'").fetchone()[0]
    assert n == 0, f"仍有 {n} 行裸码"


def test_screen_factor_cache_vt_backfilled():
    """真库守门：缓存 vt 列已回填（stock 行非空）。"""
    from src.data_platform.db import get_conn
    with get_conn() as c:
        n = c.execute("SELECT count(*) FROM screen_factor_cache WHERE vt_symbol IS NULL").fetchone()[0]
    assert n == 0
