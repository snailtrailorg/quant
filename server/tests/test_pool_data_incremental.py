"""二档池数据增量单测（U 审项 10，2026-08-20）：窗口拉取 + 游标推进 + full 校准 + duration_ms。

mock Tushare pro + DB + SyncLock，不连真实服务。
"""
import time
import pandas as pd
from datetime import date
from unittest.mock import patch, MagicMock


class _FakeLock:
    """永远抢到（测增量逻辑不测锁）。"""
    def __init__(self, *a, **kw): self.acquired = True
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows


class _FakeConn:
    """记录 execute 调用；SELECT pool_data_cursor 返回注入的游标。"""
    def __init__(self, cursors): self.cursors, self.executed = cursors, []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if sql.strip().startswith("SELECT") and "pool_data_cursor" in sql:
            return _FakeResult(list(self.cursors.items()))
        return _FakeResult([])
    def commit(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _run(cursors=None, full=False, timebox_s=280, pool=None, fail_on=None, symbols=None, fake_clock=False):
    """跑一轮同步，返回 (result, pro, conn)。fail_on=(ts_code, api) 使该调用抛错。

    隔离完备性（曾假绿教训）：sync_pools_data 内部 `import tushare_adapter` 后
    get_pro()，必须 patch 真模块 src.data_platform.adapters.tushare_adapter.get_pro，
    只 patch 函数入参的 adapter 无效。
    """
    pool = pool or ["600000.SH", "000001.SZ"]
    conn = _FakeConn(cursors or {})
    pro = MagicMock()
    pro._calls = {}

    def _mk_api(api):
        def _call(**kwargs):
            if fail_on and kwargs.get("ts_code") == fail_on[0] and api == fail_on[1]:
                raise RuntimeError("mock fail")
            pro._calls.setdefault(api, []).append(kwargs)
            return pd.DataFrame()
        return _call

    for spec_api in ["income", "balancesheet", "cashflow", "fina_indicator",
                     "cyq_chips", "top10_holders", "dividend", "pledge_stat",
                     "share_float", "stk_holdernumber"]:
        setattr(pro, spec_api, _mk_api(spec_api))

    import itertools
    clock = itertools.count(0, 0.05)  # 假钟：每次 time.time() 前进 50ms
    with patch("src.data_sync.pool_data.SyncLock", _FakeLock), \
         patch("src.data_sync.pool_data._get_pool_ts_codes", return_value=pool), \
         patch("src.data_sync.pool_data._pdb.get_conn", return_value=conn), \
         patch("src.data_sync.pool_data._upsert_rows", return_value=0), \
         patch("src.data_platform.adapters.tushare_adapter.get_pro", return_value=pro), \
         patch("src.data_sync.pool_data.time") as mt:
        mt.time.side_effect = lambda: next(clock) if fake_clock else time.time()
        from src.data_sync.pool_data import sync_pools_data
        result = sync_pools_data(timebox_s=timebox_s, full=full, symbols=symbols)
    return result, pro, conn


def _cursor_upserts(conn):
    """从 conn.executed 提取游标推进调用 {table: date}。"""
    out = {}
    for sql, params in conn.executed:
        if sql.startswith("INSERT INTO pool_data_cursor"):
            out[params[0]] = params[1]
    return out


def _sync_log_inserts(conn):
    return [(sql, p) for sql, p in conn.executed if "INSERT INTO sync_log" in sql]


TODAY = date.today().strftime("%Y%m%d")
# 增量表 4 张：dividend 窗口过滤实测无效（Tushare 返回 0 行矛盾）已撤出增量
INC_TABLES = ["income", "balancesheet", "cashflow", "fina_indicator"]


# --- 窗口拉取 ---

def test_incremental_window_applied():
    """有游标 → 增量表传 [cursor, today] 窗口；非增量表不传。"""
    _, pro, _ = _run(cursors={"income": "20260801"})
    inc = [kw for kw in pro._calls["income"]]
    assert all(kw.get("start_date") == "20260801" and kw.get("end_date") == TODAY for kw in inc)
    assert all("start_date" not in kw for kw in pro._calls["top10_holders"])
    # cyq_chips 保持当日单日模式
    assert all(kw.get("trade_date") == TODAY for kw in pro._calls["cyq_chips"])


def test_no_cursor_full_pull():
    """无游标（首轮）→ 不传窗口全量拉。"""
    _, pro, _ = _run(cursors={})
    assert all("start_date" not in kw for kw in pro._calls["income"])


def test_full_mode_ignores_cursor():
    """full=True → 有游标也全量拉（校准模式）。"""
    _, pro, _ = _run(cursors={"income": "20260801"}, full=True)
    assert all("start_date" not in kw for kw in pro._calls["income"])


# --- 游标推进 ---

def test_cursor_advance_on_complete():
    """全部标的覆盖 → 4 张增量表游标推到今天（dividend 窗口无效已撤出）。"""
    result, _, conn = _run(cursors={"income": "20260801"})
    assert result["status"] == "done"
    advanced = _cursor_upserts(conn)
    assert advanced == {t: TODAY for t in INC_TABLES}


def test_cursor_not_advance_on_error():
    """单标的单表 error → 该表游标不推（窗口保留幂等重拉），其他表照推。"""
    result, pro, conn = _run(fail_on=("000001.SZ", "income"))
    assert result["status"] == "partial"
    advanced = _cursor_upserts(conn)
    assert "income" not in advanced
    assert advanced.get("balancesheet") == TODAY


def test_cursor_not_advance_on_timebox():
    """timebox 到点中断 → 增量表未覆盖全部标的不推进。"""
    result, _, conn = _run(timebox_s=0)
    assert result["status"] == "timebox"
    assert _cursor_upserts(conn) == {}


# --- symbols 定向回补（入池触发，U 审项 9）---

def test_backfill_symbols_full_pull_no_cursor():
    """symbols 回补：只跑指定标的、无窗口全量（游标即使存在也不生效）、不推进游标。"""
    result, pro, conn = _run(cursors={"income": "20260801"}, symbols=["600519.SH"])
    assert result["status"] == "done" and result["symbols"] == 1
    # 无窗口（有 income 游标也不传）
    assert all("start_date" not in kw for kw in pro._calls["income"])
    # 只跑指定标的
    assert {kw["ts_code"] for kw in pro._calls["income"]} == {"600519.SH"}
    # 游标不动
    assert _cursor_upserts(conn) == {}
    # sync_log mode=backfill 可观测
    logs = _sync_log_inserts(conn)
    assert logs and logs[0][1][1] == "backfill"


# --- sync_log 观测 ---

def test_duration_ms_positive():
    """duration_ms 修真（原恒 0）——假钟每次调用前进 50ms，全程 >1 次调用即 >0。"""
    _, _, conn = _run(fake_clock=True)
    logs = _sync_log_inserts(conn)
    assert logs and logs[0][1][6] > 0  # 第 7 位 duration_ms


def test_error_text_in_error_column():
    """错误文本落 error 列（原错位到 failed_dates）——progress 端点读 error。"""
    result, _, conn = _run(fail_on=("000001.SZ", "income"))
    logs = _sync_log_inserts(conn)
    assert logs and "000001.SZ/income" in logs[0][1][8]   # error 列
    assert logs[0][1][9] == ""                            # failed_dates 空
