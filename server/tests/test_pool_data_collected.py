"""批 83b：池内深度数据**收编**钉 + 第二源行为级验收（4 beat 收编收尾）。

钉三组事：
1. **收编形态**：两条 beat 已从 app.py 退役；`sync_config` 两行在位且 cron 口令逐字对齐
   原 beat；两 id 都在 `_HANDLERS`（配置面 ↔ 代码路由双射由 test_sync_config_coverage 全量守）；
   长任务异步派发声明覆盖且只覆盖这两条；`sync_via_celery` 带 track 开关。
2. **入口分工**：配置驱动路径走 `sync()` 统一留痕；`pool_data_sync_task` 只剩手动两路
   （symbols 定向回补 / full 手动校准）。
3. **行为级（真库）**：注入**完整注册的第二数据源**（三注册表 + data_source 行），跑
   `run_pool_sync` → 断言 10 张真表都落了桩源的值 —— 即任务书 83b「分层判据」的可执行版：
   多源=新增 adapter 实现同族 kind 的 fetch + 注册，**引擎/编排层零改动**。
   这里不 mock 被测逻辑：真跑 sync_kind_config 读行、provider→adapter 查表、限速上下文、
   契约帧落库（通用 sink 拼列清单与 ON CONFLICT）；只有「第二数据源本身」是桩（真实第二源
   尚不存在，正是要证明的对象）。

DB 依赖：真库直查（无 dev 库自动跳过）。
"""
import inspect
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

STUB_PROVIDER = "stubsrc_b83_pool"
SYNTH_TS = "999999.SH"          # 合成标的（不撞真实数据，便于按 ts_code 清理）
FINGERPRINT = 999.25            # 桩源指纹值：落库行带它 = 数据确实来自桩源


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def _cfg(sid: str) -> dict:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        r = conn.execute("SELECT schedule, trade_day_filter, enabled, provider, sync_mode "
                         "FROM sync_config WHERE id=%s", (sid,)).fetchone()
    if not r:
        return {}
    return {"schedule": r[0], "trade_day_filter": r[1], "enabled": r[2],
            "provider": r[3], "sync_mode": r[4]}


def _kind_rows() -> dict:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT sync_id, pk_cols, float_cols, text_cols FROM sync_kind_config"
                            ).fetchall()
    return {r[0]: {"pk": list(r[1] or []), "floats": list(r[2] or []), "texts": list(r[3] or [])}
            for r in rows}


# ─────────────── 1. 收编形态 ───────────────

class TestBeatsRetired:

    def test_two_beats_gone_from_beat_schedule(self):
        from src.scheduler.app import app
        sched = app.conf.beat_schedule
        assert "pool-data-sync" not in sched
        assert "pool-data-full-calibrate" not in sched
        assert "pool_data_sync_task" not in str(sched)   # 无残留指向该任务的其他 beat

    def test_rows_present_and_enabled(self):
        """两行在位、启用、provider 恒 tushare（P1-4：非 bar 项不提供切源）。

        cron 口令等价的对照表唯一住 `test_collected_beats.TestScheduleEquivalence`
        （四条退役 beat 的等价表），此处不重复断言，只钉本切片关心的字段。
        """
        pd_row, cal_row = _cfg("pool_data"), _cfg("pool_data_full_calibrate")
        assert pd_row and cal_row, "迁移 0119 未补行"
        for row in (pd_row, cal_row):
            assert row["trade_day_filter"] == "none"
            assert row["enabled"] is True
            assert row["provider"] == "tushare"
        assert pd_row["sync_mode"] == "incremental" and cal_row["sync_mode"] == "full"
        assert pd_row["schedule"] != "manual" and cal_row["schedule"] != "manual"

    def test_both_ids_have_handlers(self):
        from src.data_sync.engine import _HANDLERS, _VIA_KIND_IDS
        for sid in ("pool_data", "pool_data_full_calibrate"):
            assert sid in _HANDLERS, sid
            assert sid not in _VIA_KIND_IDS

    def test_full_handler_differs_from_incremental(self):
        """两个 handler 由工厂产出且不是同一个闭包（full=True 才是校准）。"""
        from src.data_sync.engine import _HANDLERS
        assert _HANDLERS["pool_data"] is not _HANDLERS["pool_data_full_calibrate"]

    def test_async_dispatch_declaration_covers_exactly_the_two(self):
        """异步派发声明 ↔ 注册面交叉对账（防声明了不存在的 sync_id / 漏了长任务）。"""
        from src.scheduler.tasks import _SYNC_ASYNC_DISPATCH
        from src.data_sync.engine import _HANDLERS
        assert set(_SYNC_ASYNC_DISPATCH) == {"pool_data", "pool_data_full_calibrate"}
        assert set(_SYNC_ASYNC_DISPATCH) <= set(_HANDLERS)
        # expires 沿用原 beat options.expires（防 worker 停机期间堆积过期轮次）
        assert _SYNC_ASYNC_DISPATCH["pool_data"] == 290
        assert _SYNC_ASYNC_DISPATCH["pool_data_full_calibrate"] == 3600

    def test_sync_via_celery_has_track_switch(self):
        """track 开关在位且默认为 True（手动触发仍建任务行，行为不变）。"""
        from src.scheduler.tasks import sync_via_celery
        sig = inspect.signature(sync_via_celery.run)
        assert "track" in sig.parameters
        assert sig.parameters["track"].default is True


# ─────────────── 2. 入口分工 ───────────────

class TestEntryPoints:

    def test_handler_contract_shape(self):
        """handler 返回值适配 sync() 契约：标的数→pulled、错误→failed_dates、状态词透传。"""
        from unittest.mock import patch

        from src.data_sync import engine
        h = engine._make_pool_handler(full=False)
        fake = {"status": "partial", "symbols": 7, "saved": 42,
                "errors": ["600000.SH/income: X"], "duration_ms": 5}
        with patch("src.data_sync.pool_data.run_pool_sync", return_value=fake) as m:
            r = h({"provider": "tushare"}, "20260930")
        assert m.call_args.args[0] == {"provider": "tushare"}     # cfg 透传（provider 真路由）
        assert m.call_args.kwargs["full"] is False                # 增量 handler
        assert r["pulled"] == 7 and r["saved"] == 42
        assert r["failed_dates"] == ["600000.SH/income: X"]
        assert r["log_status"] is None            # partial 走通用口径

    def test_handler_timebox_maps_to_timeout_status_word(self):
        """时间盒中断 → log_status='timeout'（保留原独立实现的状态词，非失败也非健康心跳）。"""
        from unittest.mock import patch

        from src.data_sync import engine
        h = engine._make_pool_handler(full=True)
        with patch("src.data_sync.pool_data.run_pool_sync",
                   return_value={"status": "timebox", "symbols": 3, "saved": 1, "errors": []}):
            r = h({}, "20260930")
        assert r["log_status"] == "timeout" and r["failed_dates"] == []

    def test_handler_skipped_maps_to_skipped(self):
        from unittest.mock import patch

        from src.data_sync import engine
        h = engine._make_pool_handler(full=False)
        with patch("src.data_sync.pool_data.run_pool_sync",
                   return_value={"status": "skipped", "reason": "上轮仍在运行", "symbols": 0,
                                 "saved": 0, "errors": []}):
            assert h({}, "20260930")["log_status"] == "skipped"

    def test_task_routes_symbols_to_core_and_others_to_sync(self):
        """入口分工：symbols → run_pool_sync + log_round；full/普通 → sync(对应 sync_id)。"""
        from unittest.mock import patch

        from src.scheduler.tasks import pool_data_sync_task

        with patch("src.data_sync.pool_data.run_pool_sync",
                   return_value={"status": "done", "symbols": 1, "saved": 3, "errors": []}
                   ) as core, patch("src.data_sync.pool_data.log_round") as lg:
            pool_data_sync_task.run(symbols=["600519.SH"])
        assert core.call_args.kwargs.get("symbols") == ["600519.SH"]
        assert lg.called

        for full, want in ((False, "pool_data"), (True, "pool_data_full_calibrate")):
            with patch("src.data_sync.engine.sync",
                       return_value={"status": "success"}) as syn:
                pool_data_sync_task.run(full=full)
            assert syn.call_args[0][0] == want

    def test_tier2_heartbeat_accepts_unified_status_word(self):
        """监控心跳状态词兼容（真库）：sync() 统一留痕写 'success'，原实现写 'done'。

        收编后若心跳查询只认 'done'，tier2 会**永久误报 stale**——故名两词并收。
        插一行带标记 mode 的心跳行断言 pool_data 不被报 stale（用后即删）。
        """
        from src.data_platform.db import get_conn
        from src.scheduler.tasks import _check_tier_freshness
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO sync_log (sync_id, mode, start_date, end_date, rows_pulled, "
                "rows_saved, duration_ms, status, error, failed_dates) "
                "VALUES ('pool_data','__pytest__','','',0,0,0,'success','','')")
            conn.commit()
        try:
            stale = _check_tier_freshness()
            assert not any(r["sync_id"] == "pool_data" for r in stale), \
                "pool_data 心跳未被 'success' 命中（状态词未并收）"
        finally:
            with get_conn() as conn:
                conn.execute("DELETE FROM sync_log WHERE sync_id='pool_data' AND mode='__pytest__'")
                conn.commit()

    def test_pool_progress_endpoint_reads_failed_dates_fallback(self):
        """progress 端点两列取或：收编后错误落 failed_dates，手动路径仍落 error 列。"""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "web_api" / "routes" / "sync.py"
        text = src.read_text(encoding="utf-8")
        seg = text.split("def pool_data_progress_api")[1].split("@router")[0]
        assert "failed_dates" in seg and "r[4] or r[5]" in seg


# ─────────────── 3. 行为级：第二数据源（真库） ───────────────

def _install_stub_source():
    """注册完整第二源：adapter 注册表 + DataSource 注册表 + data_source 配置行。"""
    from src.data_platform.adapters.base import BaseDataAdapter, UnsupportedFeature, register_adapter
    from src.data_platform.data_source import DataSource

    @register_adapter
    class _StubPoolAdapter(BaseDataAdapter):
        provider = STUB_PROVIDER

        def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
            raise UnsupportedFeature("stub 只实现池内 fetch")

        def pull_minute(self, symbol, freq, start, end):
            raise UnsupportedFeature("stub 只实现池内 fetch")

        def to_bar_rows(self, df, freq, adj_map=None):
            raise UnsupportedFeature("stub 只实现池内 fetch")

        def fetch(self, req, acct=None):
            """按目标表的列形状产出一行指纹数据（列序取 adapter 自己的声明）。"""
            from datetime import datetime, timezone
            from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
            from src.quant_common.contract import ContractFrame
            spec = POOL_TABLE_SPECS.get(req.sub_kind)
            if spec is None:
                raise UnsupportedFeature(f"stub 不认 sub_kind={req.sub_kind}")
            meta = _kind_rows()[req.sub_kind]
            row = []
            for c in spec["columns"]:
                if c == "ts_code":
                    row.append(SYNTH_TS)
                elif c == "raw_json":
                    row.append("{}")
                elif c in meta["floats"]:
                    row.append(FINGERPRINT)
                elif c in meta["texts"]:
                    row.append("x")
                else:                     # 主键里的其余列（Numeric/Text 皆可接受数值字面量）
                    row.append(FINGERPRINT if c not in ("ann_date", "end_date", "trade_date",
                                                        "float_date", "div_proc", "holder_name")
                               else "20250101")
            return ContractFrame(kind=req.kind, rows=(tuple(row),), source=self.provider, freq="",
                                 fetched_at=datetime.now(tz=timezone.utc),
                                 columns=spec["columns"])

    class _StubDataSource(DataSource):
        provider = STUB_PROVIDER

        def get_client(self):
            return object()

        def test_connection(self) -> bool:
            return True

    from src.data_platform import data_source as _ds
    from src.data_platform.db import get_conn
    _ds._REGISTRY[STUB_PROVIDER] = _StubDataSource
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO data_source (name, provider, market, capabilities, position, enabled) "
            "VALUES ('批83b测试桩', %s, 'astock', '{hist_quote,ref_data}', 99, true)",
            (STUB_PROVIDER,))
        conn.commit()


def _cleanup_stub_source():
    from src.data_platform.adapters.base import _ADAPTERS
    from src.data_platform.data_source import _REGISTRY
    from src.data_platform.db import get_conn
    from src.data_sync.pool_data import POOL_TABLES
    _ADAPTERS.pop(STUB_PROVIDER, None)
    _REGISTRY.pop(STUB_PROVIDER, None)
    with get_conn() as conn:
        for table in POOL_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE ts_code=%s", (SYNTH_TS,))   # noqa: S608
        conn.execute("DELETE FROM data_source WHERE provider=%s", (STUB_PROVIDER,))
        conn.commit()


class TestSecondSourceEndToEnd:
    """验收判据（任务书 83b 分层判据的可执行版）：新增源=注册即可，编排层零改动。"""

    def test_all_ten_tables_land_stub_rows(self):
        from src.data_sync.pool_data import POOL_TABLES, run_pool_sync
        _install_stub_source()
        try:
            # symbols 路径（定向回补）：无窗口全量且**不推进真实游标**——不污染 pool_data_cursor
            r = run_pool_sync({"provider": STUB_PROVIDER}, symbols=[SYNTH_TS], timebox_s=60)
            assert r["status"] == "done", r
            assert r["saved"] == len(POOL_TABLES), r     # 10 表各落 1 行
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                for table in POOL_TABLES:
                    n = conn.execute(
                        f"SELECT count(*) FROM {table} WHERE ts_code=%s",  # noqa: S608
                        (SYNTH_TS,)).fetchone()[0]
                    assert n == 1, f"{table} 未落桩源数据（{n} 行）"
                # 指纹校验：数值列落的是桩源值（而非"碰巧 tushare 有数"）
                assert conn.execute("SELECT max(total_revenue) FROM income WHERE ts_code=%s",
                                    (SYNTH_TS,)).fetchone()[0] == FINGERPRINT
        finally:
            _cleanup_stub_source()

    def test_second_source_needs_no_engine_change(self):
        """编排层零源名分支：engine.py / pool_data.py 无 provider 字面量分支。"""
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[1] / "src" / "data_sync"
        branch = re.compile(r"(?:if|elif)\s+[^\n:]*\bprovider\b\s*(?:==|!=)\s*['\"]")
        for name in ("engine.py", "pool_data.py"):
            hits = [ln.strip() for ln in (root / name).read_text(encoding="utf-8").splitlines()
                    if branch.search(ln)]
            assert not hits, f"{name} 出现 provider 字面量分支（新增源将需改编排层）：{hits}"

    def test_stub_registration_does_not_leak(self):
        """清理面自检：注册表与 data_source 行都不该残留桩源，真表无桩行。"""
        from src.data_platform.adapters.base import _ADAPTERS
        from src.data_platform.data_source import _REGISTRY
        from src.data_platform.db import get_conn
        from src.data_sync.pool_data import POOL_TABLES
        assert STUB_PROVIDER not in _ADAPTERS
        assert STUB_PROVIDER not in _REGISTRY
        with get_conn() as conn:
            assert conn.execute("SELECT count(*) FROM data_source WHERE provider=%s",
                                (STUB_PROVIDER,)).fetchone()[0] == 0
            for table in POOL_TABLES:
                assert conn.execute(f"SELECT count(*) FROM {table} WHERE ts_code=%s",  # noqa: S608
                                    (SYNTH_TS,)).fetchone()[0] == 0, table

    def test_real_cursor_untouched_by_directed_backfill(self):
        """定向回补不推游标（真库）：池数据游标是共享状态，测试路径不得污染。"""
        from src.data_platform.db import get_conn
        _install_stub_source()
        try:
            with get_conn() as conn:
                before = {r[0]: r[1] for r in conn.execute(
                    "SELECT table_name, last_pull_date FROM pool_data_cursor").fetchall()}
            from src.data_sync.pool_data import run_pool_sync
            run_pool_sync({"provider": STUB_PROVIDER}, symbols=[SYNTH_TS], timebox_s=60)
            with get_conn() as conn:
                after = {r[0]: r[1] for r in conn.execute(
                    "SELECT table_name, last_pull_date FROM pool_data_cursor").fetchall()}
            assert after == before, "定向回补污染了真实游标"
        finally:
            _cleanup_stub_source()
