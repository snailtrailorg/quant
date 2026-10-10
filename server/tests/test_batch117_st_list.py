"""批 117：ST 官方名单 fail-closed。

任务＝`flow/任务/批117-ST官方名单fail-closed.md`（步 0 探查定案：
`pro.stock_st(trade_date=)` 官方接口存在，2026-10-09 实测全市场 201 行；
namechange `limit/offset` 分页真生效、全量恰 10000=单次上限截断实锤）。

钉的是**行为**：

1. `pull_stock_st` 透传 + 分页扩展（mock pro 断言 trade_date/limit/offset 参数与累积）。
2. `pull_namechange` 分页修复（三页 500/500/300 ⇒ 累积 1300、offset 递增；**反证钉**：
   撤掉分页循环＝单次调用 mock ⇒ 用例红——截断可检）。
3. `account_allows` ST 三态（批 117 语义）：
   - st_list 表不存在（迁移未跑）⇒ 冷启动 fail-open（按非 ST 处理）；
   - 表空（从未同步）⇒ 冷启动 fail-open；
   - 表非空 + 标的无行 ⇒ **真 fail-closed 拒**；
   - 表非空 + 标的在档（ST）+ 账户 is_st=False ⇒ 拒；is_st=True ⇒ 按权限放行；
   - 读库异常 ⇒ fail-closed 拒（既有不变量）。
4. `_derive_st_states_from_st_list`：在档⇒is_st=true、effective_from=trade_date、
   ts_code→vt_symbol 映射（600000.SH→600000.SHSE）；脏行跳过。

无库环境可跑（mock 连接），惯例同 test_account_permission。
"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.data_platform.perms import account_allows


# ---------------------------------------------------------------------------
# 1：pull_stock_st（官方名单快照拉取）
# ---------------------------------------------------------------------------
class TestPullStockSt:
    def _run(self, pages):
        """pages: 每页行数列表（返回 <500 即末页）。返回 (df, mock pro)。"""
        from src.data_platform.adapters import tushare_adapter as ta
        pro = MagicMock()
        pro.stock_st.side_effect = [pd.DataFrame(
            {"ts_code": [f"{i:06d}.SH" for i in range(off, off + n)],
             "name": ["ST测试"] * n,
             "trade_date": ["20261009"] * n,
             "type": ["S"] * n,
             "type_name": ["风险警示板"] * n})
            for off, n in zip(range(0, sum(pages) + 1, 500), pages)]
        with patch.object(ta, "get_pro", return_value=pro):
            df = ta.pull_stock_st("20261009")
        return df, pro

    def test_passthrough_single_page(self):
        """步 0 形态：全量 201 行单页——透传 + 只调一次。"""
        df, pro = self._run([201])
        assert len(df) == 201
        assert list(df.columns) == ["ts_code", "name", "trade_date", "type", "type_name"]
        assert pro.stock_st.call_count == 1
        _, kwargs = pro.stock_st.call_args
        assert kwargs["trade_date"] == "20261009"

    def test_pagination_when_over_page_limit(self):
        """行数超 500 ⇒ 自动翻页（limit/offset 留扩展被真用）。"""
        df, pro = self._run([500, 500, 300])
        assert len(df) == 1300
        assert [c.kwargs["offset"] for c in pro.stock_st.call_args_list] == [0, 500, 1000]

    def test_empty_returns_empty_df(self):
        df, pro = self._run([0])
        assert df.empty
        assert pro.stock_st.call_count == 1


# ---------------------------------------------------------------------------
# 2：pull_namechange 分页修复（步 0-b：全量恰 10000=截断实锤）
# ---------------------------------------------------------------------------
def _nc_page(off, n):
    return pd.DataFrame({
        "ts_code": [f"{off + i:06d}.BJ" for i in range(n)],
        "name": ["某股"] * n,
        "start_date": ["20200101"] * n,
        "end_date": [None] * n,
        "ann_date": ["20200101"] * n,
        "change_reason": ["更名"] * n,
    })


class TestPullNamechangePagination:
    def _run(self, pro):
        from src.data_platform.adapters import tushare_adapter as ta
        with patch.object(ta, "get_pro", return_value=pro):
            return ta.pull_namechange()

    def test_three_pages_accumulate(self):
        """三页 500/500/300 ⇒ 累积 1300 行、offset 递增 0/500/1000。"""
        pro = MagicMock()
        pro.namechange.side_effect = [_nc_page(0, 500), _nc_page(500, 500), _nc_page(1000, 300)]
        df = self._run(pro)
        assert len(df) == 1300
        assert [c.kwargs["offset"] for c in pro.namechange.call_args_list] == [0, 500, 1000]
        assert all(c.kwargs["limit"] == 500 for c in pro.namechange.call_args_list)

    def test_no_dup_between_pages(self):
        """翻页两页无交集（步 0 实测口径）。"""
        pro = MagicMock()
        pro.namechange.side_effect = [_nc_page(0, 500), _nc_page(500, 500), _nc_page(1000, 3)]
        df = self._run(pro)
        assert df["ts_code"].is_unique

    def test_anti_pin_single_call_truncates(self):
        """**反证钉**：若实现退化为单次调用（撤掉分页循环），10000 截断不可检——本用例红。

        模拟源单次上限：无 limit 参数调用恰回 10000 行（步 0 实测形态），带分页参数
        则逐页给真数据。修复后的实现走分页路径拿到全量 ⇒ 断言 >10000 行成立；
        退化实现只拿 10000 ⇒ 断言失败。
        """
        full_rows = 10300
        pro = MagicMock()

        def _side(**kwargs):
            if "limit" not in kwargs:                     # 退化路径：单次调用被 10000 截断
                return _nc_page(0, 10000)
            off = kwargs["offset"]
            n = min(kwargs["limit"], full_rows - off)
            return _nc_page(off, n) if n > 0 else pd.DataFrame()

        pro.namechange.side_effect = _side
        df = self._run(pro)
        assert len(df) == full_rows                       # 修复 ⇒ 全量；退化 ⇒ 10000 即红

    def test_empty_source(self):
        pro = MagicMock()
        pro.namechange.return_value = pd.DataFrame()
        df = self._run(pro)
        assert df.empty


# ---------------------------------------------------------------------------
# 3：account_allows ST 三态（fail-closed + 冷启动保护）
# ---------------------------------------------------------------------------
def _attr(category="stock", exchange="SHSE", board="main"):
    return SimpleNamespace(category=category, exchange=exchange, board=board)


def _perm(is_st=False, boards=("main", "star")):
    return (["stock"], ["SHSE"], list(boards), is_st, True)


class _FakeCur:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def _st_conn(exists=True, has_any=True, st_rows=(1,), exc=None, perm_is_st=False):
    """按 SQL 关键词分派的 mock 连接（perms 三查：to_regclass / 标的行 / 表空探测
    ＋ account_permission 查询）。exists=表存在；has_any=表非空；st_rows=标的在档行。"""
    conn = MagicMock()

    def execute(sql, params=()):
        if exc and "st_list" in sql:
            raise exc
        if "to_regclass" in sql:
            return _FakeCur(("st_list",) if exists else (None,))
        if "ORDER BY trade_date DESC" in sql:
            return _FakeCur((st_rows[0],) if st_rows else None)
        if "FROM st_list LIMIT 1" in sql:
            return _FakeCur((1,) if has_any else None)
        return _FakeCur(_perm(is_st=perm_is_st))     # account_permission

    conn.execute.side_effect = execute
    conn.__enter__.return_value = conn
    return conn


def _call(attr, exists=True, has_any=True, st_rows=(1,), exc=None, perm_is_st=False,
          sm_attr=None):
    sm = MagicMock()
    sm.get.return_value = sm_attr if sm_attr is not None else attr
    conn = _st_conn(exists=exists, has_any=has_any, st_rows=st_rows, exc=exc,
                    perm_is_st=perm_is_st)
    with patch("src.data_platform.security_master.SMClient", return_value=sm), \
         patch("src.data_platform.db.get_conn", return_value=conn):
        return account_allows(1, "600000.SHSE")


class TestPermsStFailClosed:
    def test_table_missing_cold_start_fail_open(self):
        """表不存在（迁移未跑）⇒ 冷启动 fail-open（非 ST 处理，放行）。"""
        assert _call(_attr(), exists=False, perm_is_st=False) is True

    def test_table_empty_cold_start_fail_open(self):
        """表空（从未同步）⇒ 冷启动 fail-open（平台未就绪≠标的无档）。"""
        assert _call(_attr(), has_any=False, st_rows=(), perm_is_st=False) is True

    def test_symbol_missing_fail_closed(self):
        """表非空但标的无行 ⇒ 真 fail-closed 拒（官方名单两态全集下的数据漂移）。"""
        assert _call(_attr(), has_any=True, st_rows=(), perm_is_st=True) is False

    def test_st_in_list_rejected_when_not_allowed(self):
        """在档 ST + 账户 is_st=False ⇒ 拒（对齐原 namechange 语义）。"""
        assert _call(_attr(), st_rows=(1,), perm_is_st=False) is False

    def test_st_in_list_allowed_when_permitted(self):
        """在档 ST + 账户 is_st=True ⇒ 按权限放行。"""
        assert _call(_attr(), st_rows=(1,), perm_is_st=True) is True

    def test_non_st_symbol_passes(self):
        """账户允许 ST ⇒ 标的放行（非 ST 标的在档=无 ST 行 ⇒ 同放行路径）。"""
        assert _call(_attr(), st_rows=(1,), perm_is_st=True) is True

    def test_read_error_fail_closed(self):
        """读库异常 ⇒ fail-closed 拒（既有不变量）。"""
        assert _call(_attr(), exc=RuntimeError("db down"), perm_is_st=True) is False

    def test_st_check_only_for_main_board(self):
        """非 main 板不走 st_list 查（star 放行，即便连接对 st_list 查询会抛错）。"""
        # star 板：attr 检查通过（SHSE+star 一致），st_list 分支不进入 ⇒ exc 不触发
        assert _call(_attr(exchange="SHSE", board="star"),
                     exc=RuntimeError("不应触达 st_list"), perm_is_st=False) is True

    def test_query_param_is_ts_form_not_vt(self):
        """步4复审 P0-1 反证钉：st_list 查询 params 必须是 tushare 形态。

        account_allows 收 vt_symbol（600000.SHSE）；st_list.ts_code 是 tushare 形态
        （600000.SH）。读侧漏转＝恒无交集＝表非空后全市场 main 板 fail-closed 全拒
        （mock 按 SQL 关键词分派不咬 params，实值须显式断言——去掉 vt_to_ts 即红）。
        """
        sm = MagicMock()
        sm.get.return_value = _attr()
        conn = _st_conn(st_rows=(1,), perm_is_st=True)
        with patch("src.data_platform.security_master.SMClient", return_value=sm), \
             patch("src.data_platform.db.get_conn", return_value=conn):
            account_allows(1, "600000.SHSE")
        # 翻 execute 调用记录：标的查询（ORDER BY trade_date DESC）那条的 params[0]
        for args, kwargs in conn.execute.call_args_list:
            sql = args[0] if args else kwargs.get("sql", "")
            if "ORDER BY trade_date DESC" in sql:
                p = (args[1] if len(args) > 1 else kwargs.get("params", ()))[0]
                assert p == "600000.SH", \
                    f"st_list 查询键须为 tushare 形态，实得 {p!r}（漏 vt_to_ts 归一？）"
                break
        else:
            raise AssertionError("未捕获标的查询（用例失效）")


# ---------------------------------------------------------------------------
# 4：_derive_st_states_from_st_list（security_state(st) 时变行派生）
# ---------------------------------------------------------------------------
class TestDeriveFromStList:
    def test_rows_and_mapping(self):
        from src.data_sync.engine import _derive_st_states_from_st_list
        captured = {}

        def _capture(rows):
            captured["rows"] = list(rows)
            return len(captured["rows"])

        df = pd.DataFrame({
            "ts_code": ["600000.SH", "000001.SZ"],
            "name": ["ST浦发", "平安银行"],
            "trade_date": ["20261009", "20261009"],
            "type": ["S", "S"],
            "type_name": ["风险警示板", "风险警示板"],
        })
        with patch("src.data_sync.engine._sm_upsert_state", side_effect=_capture):
            _derive_st_states_from_st_list(df)
        rows = captured["rows"]
        assert rows[0][0] == "600000.SHSE"          # ts_code → vt_symbol
        assert rows[0][1] == "2026-10-09"           # effective_from=快照 trade_date
        assert rows[0][2] == "st"
        assert rows[0][3]["is_st"] is True
        assert len(rows) == 2

    def test_dirty_rows_skipped(self):
        """ts_code NaN / trade_date 缺 ⇒ 跳行（fail-soft 不炸）。"""
        from src.data_sync.engine import _derive_st_states_from_st_list
        captured = {}

        def _capture(rows):
            captured["rows"] = list(rows)

        df = pd.DataFrame({
            "ts_code": [None, "600000.SH", "000001.SZ"],
            "name": ["a", "b", "c"],
            "trade_date": ["20261009", None, "bad-date"],
            "type_name": ["x", "y", "z"],
        })
        with patch("src.data_sync.engine._sm_upsert_state", side_effect=_capture):
            _derive_st_states_from_st_list(df)      # 不抛即过
        assert captured["rows"] == []


# ---------------------------------------------------------------------------
# 5：注册点齐（grep 级钉——markets / base / engine 三处）
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_three_registration_points(self):
        from src.data_platform.adapters.base import _SUPPLY_PULL
        from src.data_sync import engine
        from src.quant_common.markets import SYNC_ID_CAP_MAP

        assert _SUPPLY_PULL[("featured_daily", "st_list")] == "pull_stock_st"
        assert engine._TIER1_BATCH["st_list_sync"] == (
            "featured_daily", "st_list", "st_list", ["trade_date", "ts_code"])
        assert engine._HANDLERS["st_list_sync"] is not None
        assert SYNC_ID_CAP_MAP["st_list_sync"] == "ref_data"

    def test_schema_expectations_registered(self):
        """仓规：新表必注册列形状（schema_expectations.txt）。"""
        import pathlib
        p = pathlib.Path(__file__).resolve().parents[1] / "src/data_platform/schema_expectations.txt"
        line = [ln for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.startswith("st_list ::")]
        assert len(line) == 1
        assert line[0].split("::", 1)[1].strip().split(",") == [
            "trade_date", "ts_code", "name", "type", "type_name"]
