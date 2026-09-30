"""批 83b：池内深度数据 fetch 契约钉（adapter 声明 ↔ kind 维归置表 ↔ 契约层形状律）。

钉三件事：
1. **声明自洽**：`POOL_TABLE_SPECS` 10 表、列序非空无重复、`nums ⊆ columns`、`raw_json`
   列与标志同位、kind ∈ `POOL_FETCH_KINDS` 且是 DataKind 的合法 historical 组合。
2. **两侧对账（真库）**：每张 spec ↔ `sync_kind_config` 归置行——kind 相等、pg_table=表名、
   列集（去 raw_json）= pk ∪ float ∪ text、float_cols ⊆ nums；引擎的增量表集 ⊆ 归置
   rebuild='incremental' 行。这是「adapter 声明与 kind 维归置各说各话」的漂移闸：二者任一
   单侧改动都会红（列名改动改的是 INSERT 列清单，漂移=运行期 SQL 列不存在）。
3. **形状律 + 值归一**：`to_contract` 的非 bar 帧两律（声明列序 / 无 ts 列免 UTC 律）；
   `to_pool_rows` 值归一**逐字对齐**原 `pool_data._upsert_rows`（原 test_pool_data_upsert
   的等价性钉在此续存——raw_json 的 NaN/None 过滤与键序、nums 的 float 化、其余 str、
   None 保持）。

DB 依赖：真库直查（无 dev 库自动跳过，惯例同 test_sync_config_coverage._db_up）。
"""
import json
import math
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _kind_rows() -> dict:
    """sync_kind_config 归置行 {sync_id: {...}}（kind 维单一真相源）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT sync_id, kind, sub_kind, pg_table, pk_cols, float_cols, text_cols, rebuild "
            "FROM sync_kind_config").fetchall()
    return {r[0]: {"kind": r[1], "sub_kind": r[2], "pg_table": r[3], "pk_cols": list(r[4] or []),
                   "float_cols": list(r[5] or []), "text_cols": list(r[6] or []),
                   "rebuild": r[7]} for r in rows}


# ─────────────────── 1. 声明自洽 ───────────────────

class TestSpecDeclaration:

    def test_ten_tables_in_fixed_order(self):
        """10 表在位且顺序=0106 登记顺序（逐标的循环顺序影响 API 调用次序，保序免行为漂移）。"""
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        from src.data_sync.pool_data import POOL_TABLES
        assert POOL_TABLES == ("income", "balancesheet", "cashflow", "fina_indicator",
                               "cyq_chips", "top10_holders", "dividend", "pledge_stat",
                               "share_float", "stk_holdernumber")
        assert set(POOL_TABLE_SPECS) == set(POOL_TABLES)

    def test_columns_wellformed(self):
        """列声明非空、无重复、nums ⊆ columns、raw_json 标志与列同位。"""
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        for table, spec in POOL_TABLE_SPECS.items():
            cols = spec["columns"]
            assert cols, table
            assert len(set(cols)) == len(cols), f"{table} 列声明有重复"
            assert set(spec["nums"]) <= set(cols), f"{table} nums 有列不在 columns"
            assert ("raw_json" in cols) == bool(spec.get("raw_json")), \
                f"{table} raw_json 标志与列声明不同位"
            assert spec["window"] in ("range", "date"), table
            assert spec["api"], table

    def test_kind_is_pool_family_and_legal(self):
        """kind ∈ POOL_FETCH_KINDS 且是 DataKind 的合法 historical 组合（词表守门）。"""
        from src.data_platform.adapters.tushare_adapter import POOL_FETCH_KINDS, POOL_TABLE_SPECS
        from src.quant_common.contract import is_legal
        assert set(POOL_FETCH_KINDS) == {"financial_stmt", "featured_daily", "holder_structure"}
        for table, spec in POOL_TABLE_SPECS.items():
            assert spec["kind"] in POOL_FETCH_KINDS, table
            assert is_legal(spec["kind"], "historical"), f"{table}/{spec['kind']} 非法时点组合"

    def test_financial_tables_are_the_raw_json_four(self):
        """只有财务四表带 raw_json（原实现 raw_tables 集合）——防漏/防多。"""
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        raw = {t for t, s in POOL_TABLE_SPECS.items() if s.get("raw_json")}
        assert raw == {"income", "balancesheet", "cashflow", "fina_indicator"}

    def test_single_day_table_matches_engine_policy(self):
        """引擎侧「单日窗口」表集 ↔ adapter 侧 window='date' 声明必须一致（跨层对账）。

        引擎决定「请求哪段」（当日）；adapter 决定「怎么向源表达」（trade_date=）。两者若
        不一致：引擎给区间而源只认单日 → 静默拉到全量或报错。
        """
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        from src.data_sync.pool_data import _SINGLE_DAY_TABLES
        declared = {t for t, s in POOL_TABLE_SPECS.items() if s["window"] == "date"}
        assert declared == set(_SINGLE_DAY_TABLES)


# ─────────────────── 2. 两侧对账（真库） ───────────────────

@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestSpecVsKindRegistry:

    def test_every_table_has_kind_row(self):
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        kinds = _kind_rows()
        missing = set(POOL_TABLE_SPECS) - set(kinds)
        assert not missing, f"池内表缺 sync_kind_config 归置行（引擎取不到 kind/pg_table/pk）: {sorted(missing)}"

    def test_kind_and_pg_table_agree(self):
        """adapter 声明的 kind 与归置行 kind 相等、pg_table 落点=表名。"""
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        kinds = _kind_rows()
        for table, spec in POOL_TABLE_SPECS.items():
            row = kinds[table]
            assert spec["kind"] == row["kind"], f"{table}: adapter={spec['kind']} 归置={row['kind']}"
            assert row["pg_table"] == table, f"{table}: 归置 pg_table={row['pg_table']}"

    def test_column_set_equals_registry_typology(self):
        """列集（去 raw_json）= pk ∪ float ∪ text ——写入列清单的两侧对账。

        spec["columns"] 建 INSERT 列清单、归置行的 float/text 建重建/类型视图；列集漂移
        （单侧加列）会让 INSERT 打在不存在的列上或漏列（静默丢数）。
        """
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        kinds = _kind_rows()
        for table, spec in POOL_TABLE_SPECS.items():
            row = kinds[table]
            want = set(row["pk_cols"]) | set(row["float_cols"]) | set(row["text_cols"])
            got = set(spec["columns"]) - ({"raw_json"} if spec.get("raw_json") else set())
            assert got == want, f"{table} 列集漂移：spec-only={sorted(got - want)} 归置-only={sorted(want - got)}"

    def test_registry_float_cols_subset_of_nums(self):
        """归置行 float_cols ⊆ spec nums：adapter 至少把「归置为数值」的列做了数值化。

        反向（nums 含归置的 text 列）**是本批刻意保留的历史形态**——见 to_pool_rows
        docstring（report_type / dividend 日期列等 text 型可数值解析列）；改成严格相等会
        静默改变已入库内容，属独立的数据语义清理项。
        """
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        kinds = _kind_rows()
        for table, spec in POOL_TABLE_SPECS.items():
            missing = set(kinds[table]["float_cols"]) - set(spec["nums"])
            assert not missing, f"{table} 归置为数值但 adapter 未数值化: {sorted(missing)}"

    def test_incremental_set_subset_of_registry_incremental(self):
        """引擎的增量表集 ⊆ 归置 rebuild='incremental' 行（上界对账）。

        引擎侧是**子集**而非相等：cyq_chips 归置为 incremental（重建语义分类）但拉取侧是
        「当日退化窗口、不推游标」——相等断言会迫使当日语义换成窗口语义（行为变更）。
        """
        from src.data_sync.pool_data import _INCREMENTAL_TABLES
        kinds = _kind_rows()
        assert _INCREMENTAL_TABLES == {"income", "balancesheet", "cashflow", "fina_indicator"}
        for table in _INCREMENTAL_TABLES:
            assert kinds[table]["rebuild"] == "incremental", table
        reg_inc = {t for t, r in kinds.items() if r["rebuild"] == "incremental"}
        assert _INCREMENTAL_TABLES <= reg_inc


# ─────────────────── 3. 形状律（to_contract） ───────────────────

class TestNonBarShapeLaw:

    def test_declared_columns_enforced(self):
        """声明列序 ≠ 行长度 → ContractError（非 bar 族不再硬钉 11 字段）。"""
        from src.quant_common.contract import ContractError, to_contract
        cols = ("ts_code", "ann_date", "revenue")
        f = to_contract([("600000.SH", "20250101", 1.0)], source="t", kind="financial_stmt",
                        columns=cols)
        assert f.columns == cols and f.freq == ""
        with pytest.raises(ContractError):
            to_contract([("600000.SH", "20250101")], source="t", kind="financial_stmt",
                        columns=cols)

    def test_no_ts_column_means_no_utc_law(self):
        """非 bar 帧无 "ts" 列 → 时点列是 TEXT 日期，不受 UTC aware 律（按列名触发）。"""
        from src.quant_common.contract import to_contract
        f = to_contract([("600000.SH", "20250101", 1.0)], source="t",
                        kind="holder_structure", columns=("ts_code", "end_date", "holder_num"))
        assert f.rows[0][1] == "20250101"       # 字符串日期原样通过

    def test_bar_columns_still_enforce_utc(self):
        """bar 族语义零变化：11 字段 + ts UTC aware（含 ts 列即受律）。"""
        from datetime import datetime, timezone

        from src.quant_common.contract import BAR_COLUMNS, ContractError, to_contract
        naive = datetime(2025, 1, 1)
        row = ("600000.SHSE", "1D", naive, 1, 1, 1, 1, 1, 1, None, "tushare")
        assert len(row) == 11
        with pytest.raises(ContractError):
            to_contract([row], source="t", kind="bar_daily", freq="1D")
        aware = row[:2] + (datetime(2025, 1, 1, tzinfo=timezone.utc),) + row[3:]
        f = to_contract([aware], source="t", kind="bar_daily", freq="1D")
        assert f.columns == BAR_COLUMNS

    def test_empty_or_duplicate_columns_rejected(self):
        from src.quant_common.contract import ContractError, to_contract
        with pytest.raises(ContractError):
            to_contract([], source="t", kind="financial_stmt", columns=())
        with pytest.raises(ContractError):
            to_contract([], source="t", kind="financial_stmt", columns=("a", "a"))


# ─────────────────── 4. 值归一（原 _upsert_rows 等价性续存） ───────────────────

class TestPoolRowValues:
    """列序=spec["columns"]；值语义逐字对齐原 pool_data._upsert_rows。"""

    def test_raw_json_filter_and_order(self):
        """raw_json：键序=df 列序、NaN 与 None 被过滤、非 ascii 不转义。"""
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS, to_pool_rows
        spec = POOL_TABLE_SPECS["income"]
        df = pd.DataFrame({
            "ts_code": ["600000.SH", "600000.SH"],
            "ann_date": ["20250401", "20240401"],
            "end_date": ["20250331", "20240331"],
            "revenue": [100.5, float("nan")],
            "note": ["沪市", None],
        })
        rows = to_pool_rows(spec, df, "600000.SH")
        assert len(rows) == 2
        assert len(rows[0]) == len(spec["columns"])
        rj0 = json.loads(rows[0][spec["columns"].index("raw_json")])
        assert list(rj0) == ["ts_code", "ann_date", "end_date", "revenue", "note"]
        assert rj0 == {"ts_code": "600000.SH", "ann_date": "20250401", "end_date": "20250331",
                       "revenue": "100.5", "note": "沪市"}
        rj1 = json.loads(rows[1][spec["columns"].index("raw_json")])
        assert rj1 == {"ts_code": "600000.SH", "ann_date": "20240401", "end_date": "20240331"}

    def test_numeric_float_and_none_semantics(self):
        """nums 列：可数值化→float；NaN 仍 float(nan)（原语义不改）；缺失列→None。"""
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS, to_pool_rows
        spec = POOL_TABLE_SPECS["income"]
        cols = spec["columns"]
        df = pd.DataFrame({
            "ts_code": ["600000.SH"], "ann_date": ["20250401"], "end_date": ["20250331"],
            "revenue": [100.5],
        })
        rows = to_pool_rows(spec, df, "600000.SH")[0]
        assert rows[cols.index("revenue")] == 100.5            # nums → float
        assert rows[cols.index("report_type")] is None         # 缺失列 → None（非 'None'）
        assert rows[cols.index("total_profit")] is None
        # NaN 值走 float 分支（沿用原语义，不改行为）
        df2 = pd.DataFrame({"ts_code": ["600000.SH"], "ann_date": ["20250401"],
                            "end_date": ["20250331"], "revenue": [float("nan")]})
        assert math.isnan(to_pool_rows(spec, df2, "600000.SH")[0][cols.index("revenue")])

    def test_text_columns_stringified(self):
        """非 nums 列（pk 里的文本/数字混合）一律 str——含 cyq_chips.price（Numeric 列走
        str 由 PG 隐式转型，原实现同口径）。"""
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS, to_pool_rows
        spec = POOL_TABLE_SPECS["cyq_chips"]
        cols = spec["columns"]
        df = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20250101"],
                           "price": [12.34], "percent": [0.5]})
        row = to_pool_rows(spec, df, "600000.SH")[0]
        assert row[cols.index("price")] == "12.34"     # pk 且非 nums → str
        assert row[cols.index("percent")] == 0.5       # nums → float

    def test_empty_or_none_df(self):
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS, to_pool_rows
        spec = POOL_TABLE_SPECS["income"]
        assert to_pool_rows(spec, pd.DataFrame(), "600000.SH") == []
        assert to_pool_rows(spec, None, "600000.SH") == []


# ─────────────────── 5. adapter.fetch 池族分支 ───────────────────

def _adapter_with_pro(pro):
    """构造 TushareAdapter 并注入假 pro（不连 DB/网络）。"""
    from unittest.mock import patch

    from src.data_platform.adapters.base import TushareAdapter

    class _FakeDS:
        provider = "tushare"
        def get_client(self): return pro

    with patch("src.data_platform.data_source.get_data_source", return_value=_FakeDS()):
        return TushareAdapter()


def _req(kind, sub, sym="600000.SH", rng=None):
    from src.quant_common.contract import DataRequest
    return DataRequest(kind=kind, symbols=(sym,), temporality="historical",
                       sub_kind=sub, range_=rng, consumer_tag="sync", mode="supply")


class TestPoolFetchBranch:

    def test_fetch_returns_declared_columns_frame(self):
        from unittest.mock import MagicMock
        from src.data_platform.adapters.tushare_adapter import POOL_TABLE_SPECS
        pro = MagicMock()
        pro.income.return_value = pd.DataFrame({
            "ts_code": ["600000.SH"], "ann_date": ["20250401"], "end_date": ["20250331"],
            "revenue": [1.0]})
        adapter = _adapter_with_pro(pro)
        frame = adapter.fetch(_req("financial_stmt", "income"))
        assert frame.kind == "financial_stmt"
        assert frame.columns == POOL_TABLE_SPECS["income"]["columns"]
        assert len(frame.rows) == 1
        assert pro.income.call_args.kwargs["report_type"] == "1"     # 源参数由 spec 带出
        assert pro.income.call_args.kwargs["ts_code"] == "600000.SH"

    def test_window_to_source_params(self):
        """窗口形态：range 表给 start_date/end_date；date 表（cyq_chips）给 trade_date。"""
        from datetime import datetime
        from unittest.mock import MagicMock
        pro = MagicMock()
        pro.cyq_chips.return_value = pd.DataFrame()
        pro.pledge_stat.return_value = pd.DataFrame()
        adapter = _adapter_with_pro(pro)
        d = (datetime(2025, 1, 1), datetime(2025, 1, 31))
        adapter.fetch(_req("holder_structure", "pledge_stat", rng=d))
        assert pro.pledge_stat.call_args.kwargs == {"ts_code": "600000.SH",
                                                    "start_date": "20250101",
                                                    "end_date": "20250131"}
        adapter.fetch(_req("featured_daily", "cyq_chips", rng=d))
        assert pro.cyq_chips.call_args.kwargs == {"ts_code": "600000.SH",
                                                  "trade_date": "20250101"}   # 只认单日

    def test_no_window_means_no_window_params(self):
        """range_=None（全量）→ 不传 start_date/end_date（金融四表无游标首轮即全量）。"""
        from unittest.mock import MagicMock
        pro = MagicMock()
        pro.balancesheet.return_value = pd.DataFrame()
        adapter = _adapter_with_pro(pro)
        adapter.fetch(_req("financial_stmt", "balancesheet"))
        assert pro.balancesheet.call_args.kwargs == {"ts_code": "600000.SH", "report_type": "1"}

    def test_unknown_sub_kind_loud(self):
        from src.data_platform.adapters.base import UnsupportedFeature
        adapter = _adapter_with_pro(None)
        with pytest.raises(UnsupportedFeature):
            adapter.fetch(_req("financial_stmt", "no_such_table"))

    def test_kind_mismatch_loud(self):
        """归置行 kind 与 adapter 声明漂移时响亮拒绝，不到源上就近拉数。"""
        from src.data_platform.adapters.base import UnsupportedFeature
        adapter = _adapter_with_pro(None)
        with pytest.raises(UnsupportedFeature):
            adapter.fetch(_req("holder_structure", "income"))     # income 属 financial_stmt

    def test_pool_frame_not_11_fields_is_accepted(self):
        """池族帧列数 ≠ 11 也应合法（非 bar 形状律）——防有人把 11 字段硬钉加回来。"""
        from unittest.mock import MagicMock
        pro = MagicMock()
        pro.pledge_stat.return_value = pd.DataFrame({
            "ts_code": ["600000.SH"], "end_date": ["20250331"], "pledge_count": [3]})
        adapter = _adapter_with_pro(pro)
        frame = adapter.fetch(_req("holder_structure", "pledge_stat"))
        assert len(frame.columns) == 7 and len(frame.rows[0]) == 7
