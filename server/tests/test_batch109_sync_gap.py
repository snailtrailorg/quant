"""批 109：`sync_gap` 落表语义 ＋ 输出闭环接线 ＋ **禁读闸**（迁移 0140）。

设计依据：`flow/方案/同步窗口与参数分层-设计.md` §7.2（输出闭环 / 派生视图非第二真源）·
§5.3（封顶不得静默）· §5.4（uncertain 抑制主张）；任务文件 `flow/任务/批109-分族对账与输出闭环.md` v3。

钉的是**行为**（不是「表存在」）：

1. **锚键语义** —— UNIQUE ＝ `(sync_id, symbol, gap_start)`，**锚＝缺口起点非整区间**；整区间作
   UNIQUE 会让「缺口补了一半」产生**幽灵 open**（步 2 双盲审 P0-1）。
2. **视图式同步三路径** —— 对齐 ⇒ UPDATE gap_end 整值对齐；失配 ⇒ closed；新起点 ⇒ INSERT/重开。
3. **uncertain 抑制 ＋ 恢复** —— §5.4 不主张缺口；覆盖恢复后必须能翻正（复审 R1：否则 fresh 库
   首跑全 uncertain ⇒ 历史永久盲区）。
4. **重开＝新事件** —— `closed` 再现 ⇒ `open` ＋ `pull_count` 归零（真源是数据表，本表不承诺事件史）。
5. **两类语义互不覆盖** —— 缺口主张（open/uncertain）与排除段登记（unreachable/policy_discard）
   撞同一锚 ⇒ 只告警不改写。
6. **禁读闸** —— 窗口/期望集计算**禁读** `sync_gap`（派生值回流禁令）；`sync()` 把 `excluded`
   升级为落表终态、把仍缺段升级为聚合告警。

DB 依赖：真库行为级（无 dev 库自动跳过，惯例同 `test_batch99_sync_backfill_capability._db_up`）。
**写侧只碰 `__t109` 命名空间且用完即清**——`sync_gap` 本身是派生视图（可清空重算），但测试不污染。
"""
import ast
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

from src.data_sync import engine

NS = "__t109"          # 测试命名空间前缀（`LEFT(sync_id, len(NS))` 精确匹配，避免 `_` 通配歧义）


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def _q(sql: str, params: tuple = ()) -> list:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def _wipe() -> None:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM sync_gap WHERE LEFT(sync_id, %s) = %s", (len(NS), NS))
        conn.commit()


@pytest.fixture(autouse=True)
def _clean_ns():
    try:
        _wipe()
    except Exception:      # 无库时上面的 skipif 已接管，此处静默  # noqa: S110
        pass
    yield
    try:
        _wipe()
    except Exception:  # noqa: S110
        pass


def _rows(sid: str) -> list[tuple]:
    return [(str(r[0]), str(r[1]), r[2], r[3]) for r in _q(
        "SELECT gap_start, gap_end, state, reason FROM sync_gap WHERE sync_id=%s "
        "ORDER BY gap_start", (sid,))]


def _vsync(sid: str, symbol: str = "", gaps=None, uncertain_reason=None) -> dict:
    """视图式同步的测试封装（批 110：改调单 symbol 薄包装 `_sync_gap_sync_one`）。

    `win_lo/win_hi` 只被 uncertain-插入路径消费，此处给足缺省窗。
    """
    return engine._sync_gap_sync_one(sid, symbol, win_lo="2026-10-01", win_hi="2026-10-10",
                                     gaps=gaps, uncertain_reason=uncertain_reason)


# ---------------------------------------------------------------------------
# 1：表形态（列/约束）——迁移 0140 的契约面
# ---------------------------------------------------------------------------


class TestTableShape:
    EXPECTED_COLS = ["id", "sync_id", "symbol", "gap_start", "gap_end", "state", "reason",
                     "pull_count", "first_seen", "last_seen", "closed_at",
                     "hit_rounds", "last_hit_round"]   # 批 110 迁移 0141 追加（`false_rounds` 经步 4 盲审删列）

    @needs_db
    def test_columns_in_order(self):
        cols = [r[0] for r in _q(
            "SELECT column_name FROM information_schema.columns WHERE table_name='sync_gap' "
            "ORDER BY ordinal_position")]
        assert cols == self.EXPECTED_COLS

    @needs_db
    def test_not_null_contract(self):
        """`symbol` NOT NULL DEFAULT ''（禁 NULL：UNIQUE 对 NULL 不去重 ⇒ 锚语义失效）。"""
        d = {r[0]: r for r in _q(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name='sync_gap'")}
        for c in ("sync_id", "symbol", "gap_start", "gap_end", "state", "pull_count"):
            assert d[c][1] == "NO", f"{c} 必须 NOT NULL"
        assert d["reason"][1] == "YES" and d["closed_at"][1] == "YES"
        assert "''" in (d["symbol"][2] or ""), "symbol 默认 ''（非 NULL）"

    @needs_db
    def test_state_check_really_rejects(self):
        """五态 CHECK 真拦（不是只写在迁移里）。"""
        import psycopg
        with pytest.raises(psycopg.errors.CheckViolation):
            with engine.get_conn() as conn:
                conn.execute(
                    "INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state) "
                    "VALUES (%s,'',DATE '2026-01-01',DATE '2026-01-02','bogus')", (NS + "_chk",))

    @needs_db
    def test_unique_anchor_is_start_not_span(self):
        """UNIQUE ＝ `(sync_id, symbol, gap_start)`——**同起点不同终点也不可共存**（锚语义）。"""
        import psycopg
        with engine.get_conn() as conn:
            conn.execute("INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state) "
                         "VALUES (%s,'',DATE '2026-01-01',DATE '2026-01-05','open')", (NS + "_uq",))
            conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            with engine.get_conn() as conn:
                conn.execute("INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state) "
                             "VALUES (%s,'',DATE '2026-01-01',DATE '2026-01-09','open')",
                             (NS + "_uq",))

    @needs_db
    def test_schema_expectations_registered(self):
        """仓规：新表必注册列形状（`schema_expectations.txt`）。列序须与真库一致。"""
        p = pathlib.Path(__file__).resolve().parents[1] / "src/data_platform/schema_expectations.txt"
        line = [ln for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.startswith("sync_gap ::")]
        assert len(line) == 1
        assert line[0].split("::", 1)[1].strip().split(",") == self.EXPECTED_COLS


# ---------------------------------------------------------------------------
# 2：视图式全量重同步（唯一算子）
# ---------------------------------------------------------------------------


class TestViewSync:
    def test_open_then_close(self):
        sid = NS + "_v1"
        st = _vsync(sid, gaps=[("2026-10-02", "2026-10-03")])
        assert (st["opened"], st["new"]) == (1, [("2026-10-02", "2026-10-03")])
        assert _rows(sid) == [("2026-10-02", "2026-10-03", "open", None)]
        # 补齐 ⇒ 本轮现算为空 ⇒ 失配 ⇒ closed
        st = _vsync(sid, gaps=[])
        assert st["closed"] == 1
        assert _rows(sid)[0][2] == "closed"

    def test_aligned_anchor_rewrites_gap_end_whole_value(self):
        """对齐 ⇒ `gap_end` **整值对齐本轮现算**（不是「收缩旧值」——复审 R2：字面收缩回潮 P0-1）。"""
        sid = NS + "_v2"
        _vsync(sid, gaps=[("2026-10-02", "2026-10-04")])
        st = _vsync(sid, gaps=[("2026-10-02", "2026-10-06")])
        assert st["updated"] == 1
        assert _rows(sid) == [("2026-10-02", "2026-10-06", "open", None)], \
            "对齐路径必须写本轮现算值（含**扩张**——旧实现只允许收缩即为 P0-1 同族缺陷）"

    def test_compact_date_keys_match_existing_row(self):
        """**格式归一钉**（编码期实证缺陷）：算侧日期键是紧凑 `%Y%m%d`（`to_char(...,'YYYYMMDD')` /
        `_trade_dates_in_range` / `strftime("%Y%m%d")` 三处同源），而读侧 `date.isoformat()` 带连字符
        ⇒ 不归一到同一形态时，**既有行会被误判为「新锚」**（假 closed ＋ 假 open ＋ 假告警，per-date
        与 per-symbol 两族同族）。本测用**紧凑入参**走真实路径，必须走**对齐**而非重开。
        """
        sid = NS + "_vfmt"
        _vsync(sid, gaps=[("20261002", "20261004")])
        st = _vsync(sid, gaps=[("20261002", "20261006")])
        assert st["updated"] == 1 and st["opened"] == 0 and st["reopened"] == 0
        assert st["new"] == [], "同一锚续存 ⇒ 不得记为新 open（防告警风暴）"
        assert _rows(sid) == [("2026-10-02", "2026-10-06", "open", None)]

    def test_partial_fill_yields_new_anchor_no_ghost(self):
        """**P0-1 主判据**：缺口被补了一半 ⇒ 原锚 closed ＋ 新锚 open，**不得**留幽灵 open。"""
        sid = NS + "_v3"
        _vsync(sid, gaps=[("2026-10-02", "2026-10-06")])
        _vsync(sid, gaps=[("2026-10-04", "2026-10-06")])
        assert _rows(sid) == [
            ("2026-10-02", "2026-10-06", "closed", None),
            ("2026-10-04", "2026-10-06", "open", None),
        ]

    def test_closed_reappear_is_new_event(self):
        """`closed` 再现 ⇒ 新缺口事件：open ＋ `pull_count=0` ＋ `closed_at=NULL`（不承诺事件史）。"""
        sid = NS + "_v4"
        _vsync(sid, gaps=[("2026-10-02", "2026-10-03")])
        with engine.get_conn() as conn:
            conn.execute("UPDATE sync_gap SET pull_count=2 WHERE sync_id=%s", (sid,))
            conn.commit()
        _vsync(sid, gaps=[])
        st = _vsync(sid, gaps=[("2026-10-02", "2026-10-03")])
        assert st["reopened"] == 1 and st["new"] == [("2026-10-02", "2026-10-03")]
        row = _q("SELECT state, pull_count, closed_at FROM sync_gap WHERE sync_id=%s", (sid,))[0]
        assert (row[0], row[1], row[2]) == ("open", 0, None)

    def test_uncertain_suppresses_claims_and_recovers(self):
        """§5.4：期望集不可信 ⇒ 既有 open 转 uncertain（**保留区间**）；覆盖恢复 ⇒ closed（复审 R1）。"""
        sid = NS + "_v5"
        _vsync(sid, gaps=[("2026-10-02", "2026-10-03")])
        st = _vsync(sid, uncertain_reason="inception_unknown")
        assert st["uncertain"] == 1
        assert _rows(sid) == [("2026-10-02", "2026-10-03", "uncertain", "inception_unknown")]
        # 恢复：期望集可信且该处已非缺口 ⇒ closed（真实缺口由本轮现算自然产出 open）
        st = _vsync(sid, gaps=[])
        assert st["closed"] == 1
        assert _rows(sid)[0][2] == "closed"

    def test_uncertain_inserts_scope_row_when_empty(self):
        """无行时的 uncertain ⇒ 落一条 scope 级行（区间＝判定窗）——「只登记该族为不确定」的可见口。"""
        sid = NS + "_v6"
        _vsync(sid, uncertain_reason="stale_source")
        assert _rows(sid) == [("2026-10-01", "2026-10-10", "uncertain", "stale_source")]

    def test_no_gaps_no_claim_keeps_rows_untouched(self):
        """`gaps=None` 且非 uncertain ⇒ **不主张**（不得把既有 open 误判为 closed）。"""
        sid = NS + "_v7"
        _vsync(sid, gaps=[("2026-10-02", "2026-10-03")])
        st = _vsync(sid)
        assert (st["opened"], st["closed"]) == (0, 0)
        assert _rows(sid)[0][2] == "open"

    def test_per_symbol_scope_isolated_from_family_scope(self):
        """`symbol` 是 scope 键的一部分：同 sync_id 下标的级与家族级（`''`）互不干扰。"""
        sid = NS + "_v8"
        _vsync(sid, gaps=[("2026-10-02", "2026-10-03")])
        _vsync(sid, "BTC.BINANCE", gaps=[("2026-10-05", "2026-10-05")])
        _vsync(sid, gaps=[])
        st = _q("SELECT symbol, state FROM sync_gap WHERE sync_id=%s ORDER BY symbol", (sid,))
        assert [(r[0], r[1]) for r in st] == [("", "closed"), ("BTC.BINANCE", "open")]


# ---------------------------------------------------------------------------
# 3：终态写（excluded 落表）与两类语义互不覆盖
# ---------------------------------------------------------------------------


class TestTerminalWrite:
    def test_upsert_terminal_idempotent(self):
        sid = NS + "_t1"
        n1 = engine._upsert_sync_gap([(sid, "", "2019-09-08", "2026-09-29", "policy_discard", "策略")])
        n2 = engine._upsert_sync_gap([(sid, "", "2019-09-08", "2026-09-29", "policy_discard", "策略")])
        assert n1["inserted"] == 1 and n2["updated"] == 1
        assert len(_q("SELECT 1 FROM sync_gap WHERE sync_id=%s", (sid,))) == 1

    def test_illegal_state_rejected_loudly(self):
        """写侧唯一入口对非法态**响亮拒绝**（不依赖 DB CHECK 兜底——错误更早更可读）。"""
        with pytest.raises(ValueError):
            engine._upsert_sync_gap([(NS + "_t2", "", "2026-01-01", "2026-01-02", "bogus", None)])

    def test_terminal_excluded_not_overwritten_by_gap_claim(self):
        """排除段登记与缺口主张**撞同一锚 ⇒ 只告警不改写**（两类语义不得静默互相覆盖）。"""
        sid = NS + "_t3"
        engine._upsert_sync_gap([(sid, "", "2019-09-08", "2026-09-29", "unreachable", "源界")])
        st = _vsync(sid, gaps=[("2019-09-08", "2019-12-31")])
        assert (st["opened"], st["reopened"]) == (0, 0)
        assert _rows(sid)[0][2] == "unreachable"

    def test_symbol_scoped_excluded_row(self):
        """per-symbol 排除段带 `symbol`（scope 精确到标的）——家族级用 `''`。"""
        sid = NS + "_t4"
        engine._upsert_sync_gap([(sid, "BTC.BINANCE", "2019-11-12", "2020-01-01",
                                 "unreachable", "源可达下界（实测）")])
        assert _rows(sid) == [("2019-11-12", "2020-01-01", "unreachable", "源可达下界（实测）")]


# ---------------------------------------------------------------------------
# 4：`sync()` 接线（excluded → 终态落表；gap_dates → 聚合告警）
# ---------------------------------------------------------------------------


class _FakeLock:
    acquired = True

    def __init__(self, sid):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run_sync(handler_ret):
    fake_cfg = {"id": "astock_daily", "name": "x", "mode": "incremental", "enabled": True,
                "last_sync_date": "20260810", "last_sync_ts": None, "last_status": "idle"}
    seen = {"excluded_rows": None, "gap_alerts": []}

    def _fake_upsert(rows):
        seen["excluded_rows"] = list(rows)
        return {}

    with patch("src.data_sync.sync_lock.SyncLock", _FakeLock), \
         patch.object(engine, "_get_config", return_value=fake_cfg), \
         patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
         patch.object(engine, "_HANDLERS", {"astock_daily": MagicMock(return_value=handler_ret)}), \
         patch.object(engine, "_log"), \
         patch.object(engine, "_mark_running"), \
         patch.object(engine, "_update_sync_state"), \
         patch.object(engine, "_alert_sync_failure"), \
         patch.object(engine, "_upsert_sync_gap", side_effect=_fake_upsert), \
         patch.object(engine, "_alert_sync_gaps",
                      side_effect=lambda label, spans: seen["gap_alerts"].append((label, spans))):
        result = engine.sync("astock_daily")
    return result, seen


class TestSyncWiring:
    def test_excluded_becomes_terminal_rows(self):
        """批 108 只记日志的 `excluded` ⇒ 升级为落表终态（P1 接线面的验收点）。"""
        ret = {"pulled": 1, "saved": 1, "start": "20260811", "failed_dates": [],
               "expected_days": 1, "actual_days": 1,
               "excluded": [{"kind": "policy_discard", "symbol": "", "from": "2020-01-01",
                             "to": "2026-09-29", "reason": "策略"}]}
        _r, seen = _run_sync(ret)
        assert seen["excluded_rows"] == [
            ("astock_daily", "", "2020-01-01", "2026-09-29", "policy_discard", "策略")]

    def test_non_excluded_kind_not_written(self):
        """兜底：`kind` 不在两态内（形态漂移）⇒ 不写表（防把未知语义塞进五态 CHECK）。"""
        ret = {"pulled": 1, "saved": 1, "start": "20260811", "failed_dates": [],
               "expected_days": 1, "actual_days": 1,
               "excluded": [{"kind": "weird", "symbol": "", "from": "a", "to": "b"}]}
        _r, seen = _run_sync(ret)
        assert seen["excluded_rows"] == []

    def test_new_gap_segments_trigger_aggregated_alert(self):
        """**本轮新 open** 的段 ⇒ **一条聚合**告警（样本段），而**不进 failed_dates**（缺口≠失败）。"""
        ret = {"pulled": 1, "saved": 1, "start": "20260811", "failed_dates": [],
               "expected_days": 2, "actual_days": 1, "gap_dates": ["20260812"],
               "reconcile": {"segments": [("20260812", "20260812")],
                             "new_segments": [("20260812", "20260812")]}}
        result, seen = _run_sync(ret)
        assert seen["gap_alerts"] == [("astock_daily", ["20260812~20260812"])]
        assert result["status"] == "success", "缺口不得改终态词（游标语义不变）"

    def test_persistent_gap_does_not_realert(self):
        """🔴 **只响新 open**（方案 v3 产出 3）：存量缺口每轮仍缺 ⇒ 告警**不得**重响。

        判据面＝`new_segments`（视图式同步判定的新 open）**而非** `segments`（本轮仍缺全部段）。
        若误用后者，长期 open 的存量缺口会每轮重响 ⇒ 告警疲劳、真缺口可见性被稀释；且与
        per-symbol 的 `stat['new']` 纪律分叉（同一纪律两族两套实现＝单一真源破损）。
        """
        ret = {"pulled": 1, "saved": 1, "start": "20260811", "failed_dates": [],
               "expected_days": 2, "actual_days": 1, "gap_dates": ["20260812"],
               "reconcile": {"segments": [("20260812", "20260812")], "new_segments": []}}
        _r, seen = _run_sync(ret)
        assert seen["gap_alerts"] == [], "存量缺口续存 ⇒ 不是新 open，不得响铃"

    def test_no_gap_no_alert(self):
        ret = {"pulled": 1, "saved": 1, "start": "20260811", "failed_dates": [],
               "expected_days": 1, "actual_days": 1, "reconcile": {"segments": []}}
        _r, seen = _run_sync(ret)
        assert seen["gap_alerts"] == []


# ---------------------------------------------------------------------------
# 5：禁读闸（派生值回流禁令）＋ RUNBOOK 注册
# ---------------------------------------------------------------------------


class TestNoBackflowGate:
    def test_engine_only_writes_never_reads_in_window_math(self):
        """**禁读闸**：`sync_gap` 的字面量只许出现在四个写侧函数的 SQL 里。

        设计 §7.2：`sync_gap` 是**派生视图**，真源是数据表 ＋ SM ＋ 窗口规则；任何窗口/期望集
        计算读它 ⇒ 派生值回流成真源（本表可清空重算的前提即此禁令）。本闸用 AST 钉住：
        除白名单外，**任何函数体内的 SQL 字面量**（docstring 除外）不得触 `sync_gap` 表。
        """
        allowed = {"_gap_put", "_upsert_sync_gap", "_sync_gap_sync", "_list_repullable",
                   "_sync_gap_scope_rows"}   # 批 110：scope 级读取口（写侧的读前一步）
        pat = re.compile(r"\b(FROM|INTO|UPDATE|JOIN|TABLE)\s+sync_gap\b")
        p = pathlib.Path(engine.__file__)
        tree = ast.parse(p.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]                      # 去 docstring
            for sub in ast.walk(ast.Module(body=body, type_ignores=[])):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and pat.search(sub.value):
                    found.add(node.name)
        assert found <= allowed, f"`sync_gap` 表被非写侧函数触碰：{sorted(found - allowed)}"

    def test_gap_layer_symbols_confined_to_engine(self):
        """**跨模块禁读**：缺口层私有符号（写侧 4 函数）不得被 `engine` 之外的模块引用。

        原闸只扫 `engine.py` 单文件 ＋ SQL 字面量正则 ⇒ 「**别的模块** import 缺口层符号去算窗口/
        期望集」这条回流路径**不被覆盖**（步 4 双盲审 A 指认的覆盖面缺口）。本条补上：全 `src/` 扫描。
        ⚠️ 本节纪律只管「**计算侧**不得回流」；展示侧（DataOps 看板等）日后直查 `sync_gap` 出指标是
        **合法**的（长驻 open 行数＝存量缺口指标，设计 §7.2），故**不**用「凡触表即红」的粗规则。
        """
        banned = ("_sync_gap_sync", "_upsert_sync_gap", "_gap_put", "_list_repullable")
        engine_p = pathlib.Path(engine.__file__).resolve()
        src_root = engine_p.parents[1]
        offenders: dict[str, list[str]] = {}
        for p in sorted(src_root.rglob("*.py")):
            if p.resolve() == engine_p:
                continue
            text = p.read_text(encoding="utf-8")
            hit = sorted(n for n in banned if n in text)
            if hit:
                offenders[str(p.relative_to(src_root))] = hit
        assert not offenders, f"缺口层符号泄漏到 engine 之外：{offenders}"

    def test_window_functions_do_not_reference_gap_layer(self):
        """窗口/期望集计算链（含对账自己的读取口）不得引用 `sync_gap` 任何符号。"""
        p = pathlib.Path(engine.__file__)
        tree = ast.parse(p.read_text(encoding="utf-8"))
        banned = {"sync_gap", "_sync_gap_sync", "_upsert_sync_gap", "_gap_put", "_list_repullable"}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                    "_window_floors", "_family_start", "_crypto_end", "_crypto_base_start",
                    "_trade_dates_in_range", "_local_dates", "_to_segments", "_get_config"):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                assert not (names & banned), f"{node.name} 引用了缺口层符号"

    def test_runbook_registered(self):
        """仓规：新 alert code → RUNBOOK（直调打码点 ⊆ RUNBOOK 键，test_notify 同源一致性）。"""
        from src.alert_notify.runbook import RUNBOOK
        assert "sync.gap" in RUNBOOK
        assert RUNBOOK["sync.gap"]["label"] and RUNBOOK["sync.gap"]["guide"]

    def test_alert_code_literal_matches_runbook(self):
        """`_alert_sync_gaps` 实际打码的 code 字面量 == RUNBOOK 键（防改码不同步）。"""
        p = pathlib.Path(engine.__file__).read_text(encoding="utf-8")
        assert 'code="sync.gap"' in p
