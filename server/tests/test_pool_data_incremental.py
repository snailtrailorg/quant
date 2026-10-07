"""二档池数据编排单测（U 审项 10，2026-08-20；批 83b 收编后重定向）。

批 83b 收编把「拉取」下沉 adapter.fetch 契约，本文件的挂点随之改变：
- **不再** patch `pool_data._upsert_rows` / `tushare_adapter.get_pro`（那些函数已随拉取下移
  或删除）；改为注入**假 adapter**（`fetch(req)` 记录请求、按需返回契约帧或抛错）——
  断言面从「源 API kwargs」上移到「**DataRequest**（kind/sub_kind/range_）」+「落库 SQL」。
- 拉取仍经 `rate_limit_context(ds, 表名)`（批 64b 语义原样保留）→ 限速键与熔断记账的钉不变。
- **DB 依赖**：`_read_sync_kind` 走真库（kind/pg_table/pk_cols 是 kind 维真相源，伪造就等于
  测试自己编真相）；无 dev 库自动跳过（惯例同 test_sync_config_coverage._db_up）。

未变的语义（逐条对应原测试）：窗口拉取、游标推进/不推进、full 校准、symbols 定向回补、
错误聚合、限速键=表名、熔断连续失败开闸。
sync_log 留痕已移交给 engine.sync()（见 test_pool_data_collected）；手动路径的 log_round
口径在本文件尾部单测。
"""
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.quant_common.contract import ContractFrame, DataRequest


class _AllPresent(set):
    """`engine._local_dates` 替身：声称本地已有**全部**期望日。

    批 109 起 tier1/per-date handler 收尾会做日期级对账（读本地 → 差集 → 内联重拉）。本文件的
    用例只钉「拉取循环与限速键」，**不**想被对账真读 dev 库（那会让每个期望日都判为缺 ⇒ 重复拉 +
    往 `sync_gap` 落行）。⇒ 以「本地全在」隔离对账：缺口为空 ⇒ 零重拉、零落表。
    """

    def __contains__(self, _item):  # noqa: D105
        return True


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


class _FakeLock:
    """永远抢到（测增量逻辑不测锁）。"""

    def __init__(self, *a, **kw):
        self.acquired = True
        self.key = a[0] if a else ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows


class _FakeCursor:
    def __init__(self): self.executed = []
    def executemany(self, sql, rows): self.executed.append((sql, list(rows)))
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    """记录 execute/cursor 调用；SELECT pool_data_cursor 返回注入的游标。"""
    def __init__(self, cursors):
        self.cursors, self.executed, self.sunk = cursors, [], []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if sql.strip().startswith("SELECT") and "pool_data_cursor" in sql:
            return _FakeResult(list(self.cursors.items()))
        return _FakeResult([])
    def cursor(self):
        cur = _FakeCursor()
        self.sunk.append(cur)
        return cur
    def commit(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeDS:
    """限速/熔断替身（批 64b）：间隔 0 不等待；无 get_param_float → 熔断参数走代码默认。"""
    provider = "tushare"
    def get_rate_limit(self, api_name): return 0.0


class _FakeAdapter:
    """假数据源：记录 DataRequest；fail_on=(ts_code, table) 时该次抛错；rows_n>0 时回契约帧。"""
    provider = "tushare"

    def __init__(self, fail_on=None, fail_all=False, rows_n=0):
        self.fail_on, self.fail_all, self.rows_n = fail_on, fail_all, rows_n
        self.reqs: list[DataRequest] = []

    def fetch(self, req, acct=None):
        self.reqs.append(req)
        sym = req.symbols[0]
        if self.fail_all or (self.fail_on and sym == self.fail_on[0]
                             and req.sub_kind == self.fail_on[1]):
            raise RuntimeError("mock fail")
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        cols = POOL_TABLE_SPECS[req.sub_kind]["columns"]
        rows = tuple(("x",) * len(cols) for _ in range(self.rows_n))
        return ContractFrame(kind=req.kind, rows=rows, source=self.provider, freq="",
                             fetched_at=datetime.now(), columns=cols)

    def reqs_of(self, table):
        return [r for r in self.reqs if r.sub_kind == table]


def _run(cursors=None, full=False, timebox_s=280, pool=None, fail_on=None, symbols=None,
         fake_clock=False, ctx=None, fail_all=False, rows_n=0):
    """跑一轮，返回 (result, adapter, conn)。fake_clock=假钟（每次 time.time() 前进 50ms）。

    隔离点：SyncLock（假锁）/ 池标的查询 / pool_data 自己的 DB 连接（游标 + 落库）/ 限速 ds /
    adapter 解析。`_read_sync_kind` **不 mock**——归置行走真库（kind 维真相源）。
    """
    pool = ["600000.SH", "000001.SZ"] if pool is None else pool
    conn = _FakeConn(cursors or {})
    adapter = _FakeAdapter(fail_on=fail_on, fail_all=fail_all, rows_n=rows_n)

    import itertools
    clock = itertools.count(0, 0.05)
    from src.data_platform.rate_limit import rate_limit_context as _real_ctx
    with patch("src.data_sync.pool_data.SyncLock", _FakeLock), \
         patch("src.data_sync.pool_data._get_pool_ts_codes", return_value=pool), \
         patch("src.data_sync.pool_data._pdb.get_conn", return_value=conn), \
         patch("src.data_sync.pool_data._get_kline_adapter", return_value=adapter), \
         patch("src.data_sync.pool_data._get_rate_ds", return_value=_FakeDS()), \
         patch("src.data_sync.pool_data.rate_limit_context", ctx or _real_ctx), \
         patch("src.data_sync.pool_data.time") as mt:
        mt.time.side_effect = lambda: next(clock) if fake_clock else time.time()
        from src.data_sync.pool_data import run_pool_sync
        result = run_pool_sync(None, full=full, symbols=symbols, timebox_s=timebox_s)
    return result, adapter, conn


def _cursor_upserts(conn):
    """从 conn.executed 提取游标推进调用 {table: date}。"""
    return {params[0]: params[1] for sql, params in conn.executed
            if sql.startswith("INSERT INTO pool_data_cursor")}


# 批 92：池数据窗口上界改「昨日自然日」（`_POOL_OVERLAP_DAYS`=7 天回看），游标亦推进到该上界
YESTERDAY = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
INC_TABLES = ["income", "balancesheet", "cashflow", "fina_indicator"]


# ─────────────── 窗口拉取（断言面上移到 DataRequest.range_） ───────────────

@needs_db
class TestWindow:

    def test_incremental_window_applied(self):
        """有游标 → 增量表请求 range_=[cursor-回看7天, 上界(昨日)]；非增量表 range_=None。"""
        _, ad, _ = _run(cursors={"income": "20260801"})
        exp_start = datetime(2026, 7, 25)                 # 20260801 - 7 天回看（批 92）
        exp_end = datetime.strptime(YESTERDAY, "%Y%m%d")  # 上界=昨日自然日（批 92）
        for r in ad.reqs_of("income"):
            assert r.range_ == (exp_start, exp_end)
        assert all(r.range_ is None for r in ad.reqs_of("top10_holders"))
        # cyq_chips 单日模式（引擎给 (上界, 上界)，源侧译成 trade_date=）
        td = exp_end
        assert all(r.range_ == (td, td) for r in ad.reqs_of("cyq_chips"))

    def test_request_shape_is_supply_contract(self):
        """请求形态：kind 由归置行给、sub_kind=表名、单标的、supply 模式。"""
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        _, ad, _ = _run(pool=["600000.SH"])
        r = ad.reqs_of("income")[0]
        assert r.kind == POOL_TABLE_SPECS["income"]["kind"] == "financial_stmt"
        assert r.sub_kind == "income" and r.symbols == ("600000.SH",)
        assert r.mode == "supply" and r.consumer_tag == "sync" and r.temporality == "historical"
        assert ad.reqs_of("cyq_chips")[0].kind == "featured_daily"
        assert ad.reqs_of("dividend")[0].kind == "holder_structure"

    def test_no_cursor_full_pull(self):
        """无游标（首轮）→ range_=None 全量。"""
        _, ad, _ = _run(cursors={})
        assert all(r.range_ is None for r in ad.reqs_of("income"))

    def test_full_mode_ignores_cursor(self):
        """full=True → 有游标也不给窗口（校准模式）。"""
        _, ad, _ = _run(cursors={"income": "20260801"}, full=True)
        assert all(r.range_ is None for r in ad.reqs_of("income"))


# ─────────────── 游标推进 ───────────────

@needs_db
class TestCursor:

    def test_cursor_advance_on_complete(self):
        result, _, conn = _run(cursors={"income": "20260801"})
        assert result["status"] == "done"
        assert _cursor_upserts(conn) == {t: YESTERDAY for t in INC_TABLES}

    def test_cursor_not_advance_on_error(self):
        result, _, conn = _run(fail_on=("000001.SZ", "income"))
        assert result["status"] == "partial"
        adv = _cursor_upserts(conn)
        assert "income" not in adv and adv.get("balancesheet") == YESTERDAY

    def test_cursor_not_advance_on_timebox(self):
        result, _, conn = _run(timebox_s=0)
        assert result["status"] == "timebox"
        assert _cursor_upserts(conn) == {}

    def test_symbols_backfill_no_cursor_advance(self):
        result, ad, conn = _run(cursors={"income": "20260801"}, symbols=["600519.SH"])
        assert result["symbols"] == 1 and _cursor_upserts(conn) == {}
        assert all(r.range_ is None for r in ad.reqs_of("income"))
        assert {r.symbols[0] for r in ad.reqs_of("income")} == {"600519.SH"}


# ─────────────── 落库（通用 sink：列序来自帧、主键来自归置行） ───────────────

@needs_db
class TestGenericSink:

    def test_rows_landed_via_frame_columns_and_registry_pk(self):
        """落库 SQL 由 frame.columns（producer 声明）+ 归置行 pk_cols 拼——引擎零表名硬编码。"""
        _, _, conn = _run(pool=["600000.SH"], rows_n=2)
        sqls = [sql for cur in conn.sunk for sql, _ in cur.executed]
        inc = [s for s in sqls if s.startswith("INSERT INTO income ")]
        assert inc, "income 帧未落库"
        assert "ON CONFLICT (ts_code, ann_date, end_date) DO UPDATE SET" in inc[0]
        assert "raw_json=EXCLUDED.raw_json" not in inc[0]      # 全列存档列不参与覆写
        batches = [rows for cur in conn.sunk for _, rows in cur.executed
                   if len(rows) == 2]
        assert batches, "2 行帧未按一次 executemany 提交（批量优化丢失）"

    def test_registry_pg_table_used(self):
        """写库目标是归置行 pg_table（本测试用表名同名；防有人把表名写死回代码）。"""
        _, _, conn = _run(pool=["600000.SH"], rows_n=1)
        sqls = [sql for cur in conn.sunk for sql, _ in cur.executed]
        for table in ("income", "cyq_chips", "stk_holdernumber"):
            assert any(s.startswith(f"INSERT INTO {table} ") for s in sqls), table


# ─────────────── full / symbols 模式 ───────────────

@needs_db
class TestModes:

    def test_full_pulls_all_tables_without_window(self):
        result, ad, _ = _run(pool=["600000.SH"], full=True)
        assert result["status"] == "done"
        from src.data_sync.pool_data import POOL_TABLES
        assert {r.sub_kind for r in ad.reqs} == set(POOL_TABLES)      # 10 表全覆盖
        assert len(ad.reqs) == len(POOL_TABLES)                       # 单标的一轮 = 10 次拉取

    def test_symbols_limited_to_given(self):
        _, ad, _ = _run(pool=["600000.SH", "000001.SZ"], symbols=["600519.SH"])
        assert {r.symbols[0] for r in ad.reqs} == {"600519.SH"}

    def test_idle_when_no_pool_symbols(self):
        result, ad, _ = _run(pool=[])
        assert result["status"] == "idle" and not ad.reqs

    def test_skipped_when_lock_held(self):
        from src.data_sync.pool_data import run_pool_sync

        class _BusyLock(_FakeLock):
            def __init__(self, *a, **kw): self.acquired = False

        with patch("src.data_sync.pool_data.SyncLock", _BusyLock):
            r = run_pool_sync(None)
        assert r["status"] == "skipped" and "上轮仍在运行" in r["reason"]

    def test_resource_lock_key_differs_from_sync_ids(self):
        """资源锁键不得等于任一 sync_id：engine.sync() 已持 SyncLock(sync_id)，
        同名即自锁死（该轮永远拿不到锁 → 池数据永不运行）。"""
        from src.data_sync.pool_data import _RESOURCE_LOCK
        assert _RESOURCE_LOCK not in ("pool_data", "pool_data_full_calibrate")


# ─────────────── 限速收编 + 熔断（批 64b 语义原样保留） ───────────────

@needs_db
class TestRateLimitAndBreaker:

    def test_rate_limit_wrapped_per_table(self):
        """10 表拉取各经 rate_limit_context(ds, 表名)——键=限速档键（防漏防错键）。"""
        seen = []

        @contextmanager
        def spy(ds, api_name, min_interval=None):
            seen.append(api_name)
            yield

        _run(pool=["600000.SH"], ctx=spy)
        assert set(seen) == {"income", "balancesheet", "cashflow", "fina_indicator",
                             "cyq_chips", "top10_holders", "dividend", "pledge_stat",
                             "share_float", "stk_holdernumber"}

    def test_rate_limit_failure_counted_not_fatal(self):
        """拉取异常经上下文穿透（熔断记账）但被外层捕获——轮次仍产出 partial，不中断。

        单点失败被 interleaved success 重置（熔断=连续失败语义，终态 closed 正确）。
        """
        result, _, _ = _run(pool=["600000.SH", "000001.SZ"], fail_on=("000001.SZ", "income"))
        assert result["status"] == "partial"
        from src.data_platform import rate_limit
        assert rate_limit._BREAKERS[("tushare", "0")].state == "closed"

    def test_circuit_breaker_opens_on_consecutive_failures(self):
        """全表连续失败 ≥5 → tushare 破坏体开闸（去 with 包装则永不参与=行为级钉）。"""
        result, _, _ = _run(pool=["600000.SH"], fail_all=True)
        assert result["status"] == "partial"
        from src.data_platform import rate_limit
        assert rate_limit._BREAKERS[("tushare", "0")].state == "open"

    def test_missing_kind_row_is_counted_not_silent(self):
        """归置行缺失 → 该表**不进循环**且错误入列（原实现会 KeyError 崩整轮）。"""
        with patch("src.data_sync.pool_data._read_sync_kind",
                   side_effect=lambda t: {} if t == "income" else {
                       "kind": "pill", "pg_table": t, "pk_cols": ["ts_code"]}):
            result, ad, _ = _run(pool=["600000.SH"])
        assert "income" not in {r.sub_kind for r in ad.reqs}
        assert any("income" in e and "归置行" in e for e in result["errors"])
        assert result["status"] == "partial"


# ─────────────── 手动路径 sync_log 留痕（原 _log 口径续存） ───────────────

@needs_db
class TestLogRound:
    """log_round：手动/定向回补的留痕（配置驱动路径由 engine.sync() 留痕）。"""

    def _logged(self, result, mode="backfill"):
        """跑 log_round，返回 sync_log 的 INSERT 调用 [(sql, params), ...]。

        挂点两处：`pool_data._pdb.get_conn`（本模块自身）与 `engine.get_conn`（`engine._log`
        的模块级绑定——patch 前一处的模块属性管不到已绑定的后一处）。
        """
        from src.data_sync.pool_data import log_round
        conn = MagicMock()
        conn.__enter__.return_value = conn      # MagicMock 的 __enter__ 默认返回新 mock——须钉回自身
        with patch("src.data_sync.engine.get_conn", return_value=conn), \
             patch("src.data_sync.pool_data._pdb.get_conn", return_value=conn):
            log_round(result, mode=mode)
        return [c[0] for c in conn.execute.call_args_list
                if "INSERT INTO sync_log" in str(c[0][0])]

    def test_writes_error_column_not_failed_dates(self):
        """错误文本落 error 列（progress 端点读 error）、failed_dates 留空、mode 可观测。"""
        ins = self._logged({"status": "partial", "symbols": 2, "saved": 5,
                            "errors": ["000001.SZ/income: RuntimeError: mock fail"],
                            "duration_ms": 12})
        assert ins, "log_round 未写 sync_log"
        _, params = ins[0]
        assert params[0] == "pool_data" and params[1] == "backfill"
        assert params[4] == 2 and params[5] == 5          # rows_pulled=标的数 / rows_saved
        assert params[6] == 12                            # duration_ms 真实
        assert "000001.SZ/income" in params[8]            # error 列
        assert params[9] in ([], "")                      # failed_dates 空（engine._log 落 ''）

    def test_status_vocabulary_preserved(self):
        """状态词逐字保留：timebox→timeout、skipped→skipped、errors→partial、否则 done。"""
        cases = [({"status": "timebox", "errors": []}, "timeout"),
                 ({"status": "skipped", "reason": "上轮仍在运行", "errors": []}, "skipped"),
                 ({"status": "partial", "errors": ["x"]}, "partial"),
                 ({"status": "done", "errors": []}, "done")]
        for result, want in cases:
            ins = self._logged({**result, "symbols": 1, "saved": 0, "duration_ms": 1})
            assert ins and ins[0][1][7] == want, result

    def test_skipped_records_reason_in_error_column(self):
        """撞锁轮次的 reason 落 error 列（原实现同）——排障能看出是撞锁而非数据失败。"""
        ins = self._logged({"status": "skipped", "reason": "上轮仍在运行", "symbols": 0,
                            "saved": 0, "errors": [], "duration_ms": 0})
        assert "上轮仍在运行" in ins[0][1][8]

    def test_idle_not_logged(self):
        """无池标的（idle）不留痕——与原实现提前返回同口径（不虚报一轮心跳）。"""
        assert self._logged({"status": "idle", "symbols": 0, "saved": 0, "errors": []}) == []


# ─────────────── engine 工厂 handler 的限速键（与池数据同款口径） ───────────────

def test_engine_tier1_rate_key_is_table():
    """批 64b（engine 收编）：tier1 上下文键=table（per-API 档）而非 "daily"。"""
    import pandas as pd

    from src.data_sync import engine
    seen = []

    @contextmanager
    def spy(ds, api_name, min_interval=None):
        seen.append(api_name)
        yield

    empty = pd.DataFrame()

    def _fake_pull(**kw):          # 替身源拉取（批 100：经 adapter.fetch_supply 分派到模块 pull）
        return empty

    with patch("src.data_platform.rate_limit.rate_limit_context", spy), \
         patch("src.data_platform.data_source.get_data_source", return_value=_FakeDS()), \
         patch.object(engine, "_local_dates", return_value=_AllPresent()), \
         patch("src.data_platform.adapters.tushare_adapter.pull_stk_limit", new=_fake_pull):
            h = engine._make_tier1_handler("stk_limit", None, "stk_limit", ["trade_date", "ts_code"], [])
            # 窗口非空才进循环：给足够早的游标，防「长节假日 + 邻近今日」把窗口压空 ⇒ 假红
            # （原传 {} 依赖 today-3：遇 10-01~10-08 类整段休市，end_date 恒节前 ⇒ start>end 早退）
            _cfg = {"last_sync_date": (date.today() - timedelta(days=10)).strftime("%Y%m%d")}
            r = h(_cfg, date.today().strftime("%Y%m%d"))
    assert seen and set(seen) == {"stk_limit"}
    assert r["failed_dates"] == []


def test_engine_full_rebuild_rate_key_is_table():
    """批 64b（engine 收编）：full_rebuild 工厂（namechange/concept）拉取经上下文，键=table。"""
    import pandas as pd

    from src.data_sync import engine
    seen = []

    @contextmanager
    def spy(ds, api_name, min_interval=None):
        seen.append(api_name)
        yield

    def _fake_pull(**kw):
        return pd.DataFrame()

    with patch("src.data_platform.rate_limit.rate_limit_context", spy), \
         patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
         patch("src.data_platform.data_source.get_data_source", return_value=_FakeDS()), \
         patch("src.data_platform.adapters.tushare_adapter.pull_namechange", new=_fake_pull):
        h = engine._make_full_rebuild_handler("static_list", "namechange", "namechange",
                                              ["ts_code", "name", "start_date"], [])
        r = h({}, "20260808")
    assert seen == ["namechange"]
    assert r["failed_dates"] == ["空数据"]
