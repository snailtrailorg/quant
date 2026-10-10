"""批 115·步 2 · 读侧切换（V2）——float/text_cols 单源化 `sync_kind_config`。

验收（任务文件 V2 的单测面）：
1. **读侧来源=DB 行**：mock `get_conn` 返回族层行 ⇒ tier1 handler 的 upsert 列集合/
   值归一取自 DB 行（而非 import 常量——常量已删，靠本测钉住「真读 DB」）。
2. **反证**：同一 mock 下把行改成不同列 ⇒ 写入形状跟着变（= 读侧真挂在 DB 上）；
   归置行缺失 ⇒ 响亮 RuntimeError（不静默退空列写裸主键行）。
3. **import 零触库**：cols 解析延迟到 handler 首调（批 107 立法「engine import 时不得触库」）。
4. 源码级：`engine.py` 无 `_TIER1_FLOAT_COLS`/`_TIER1_TEXT_COLS` 残留；注册项全部
   `cols_from_sync_kind=True`（防新增 tier1 项漏旗回落到隐式列）。
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_sync import engine


class _FakeDS:
    provider = "test"

    def record_usage(self, **kw):
        return None

    def get_rate_limit(self, api_name):
        return 0.0


class _FakeCursor:
    def __init__(self, rows):
        # rows: exec 待返回值队列（每次 fetchone 弹一个）
        self._rows = list(rows)
        self.executed: list[tuple] = []
        self.last_sql = ""
        self.last_batch = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def executemany(self, sql, batch):
        self.executed.append(("EXECMANY", batch))
        self.last_sql = sql
        self.last_batch = list(batch)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cur = cursor

    def cursor(self):
        return self._cur

    def execute(self, *a, **kw):
        return self._cur.execute(*a, **kw)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _placement_row(float_cols, text_cols):
    # sync_kind_config 行（_read_sync_kind SELECT 的 7 列序）
    return ("featured_daily", "moneyflow", "moneyflow", False,
            ["ts_code", "trade_date"], float_cols, text_cols)


class TestReadsFromPlacementRows:
    """消费点断言：handler 的列形状取自 `sync_kind_config` 行（mock DB），非代码常量。"""

    def _run(self, float_cols, text_cols):
        """构造注册路径同款 handler（cols_from_sync_kind=True）+ mock DB/源，返回 (sql, batch, calls)。"""
        cur = _FakeCursor([_placement_row(float_cols, text_cols)])
        calls = []

        class _Ad:
            provider = "fake"

            def fetch_supply(self, kind, sub_kind=None, **kw):
                calls.append((kind, sub_kind, kw))
                # 源帧带 float+text+pk 三类列（多带一列 pk）——看哪些列被写、值形态如何
                return pd.DataFrame({
                    "ts_code": ["000001.SZ"], "trade_date": ["20260930"],
                    "buy_sm_vol": ["123.5"], "net_mf_vol": ["-7.0"],
                    "note_txt": ["x"], "unlisted_junk": ["SHOULD_NOT_APPEAR"],
                })

        h = engine._make_tier1_handler(
            "featured_daily", "moneyflow", "moneyflow", ["ts_code", "trade_date"],
            float_cols=["WRONG"], text_cols=["WRONG"],          # 入参错误值——注册路径应忽略
            lag_trade_days=0, date_param="trade_date", sync_id="moneyflow_sync",
            cols_from_sync_kind=True)
        with patch.object(engine, "_provider_of", return_value="fake"), \
             patch.object(engine, "_get_supply_adapter", return_value=_Ad()), \
             patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
             patch.object(engine, "_data_ready_end_date", return_value="20260930"), \
             patch.object(engine, "_family_start", return_value=(__import__("datetime").date(2026, 1, 1), [])), \
             patch.object(engine, "_trade_dates_in_range", return_value=["20260930"]), \
             patch.object(engine, "_local_dates", return_value={"20260930"}), \
             patch.object(engine, "_reconcile_dates",
                          return_value={"gap_dates": [], "new_segments": []}), \
             patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
             patch.object(engine, "get_conn", return_value=_FakeConn(cur)), \
             patch("src.data_platform.db.get_conn", return_value=_FakeConn(cur)):
            r = h({"id": "moneyflow_sync", "provider": "fake",
                   "last_sync_date": "20260929"}, "20260930")
        assert r["failed_dates"] == [], r
        return cur, calls

    def test_upsert_cols_come_from_db_row(self):
        """DB 行声明 (buy_sm_vol float / note_txt text) ⇒ upsert 列集=PK+这两列；
        值归一随列（float 列过 _safe_float=0.0 兜底、text 列 str）。"""
        cur, _ = self._run(["buy_sm_vol"], ["note_txt"])
        sql = cur.last_sql
        assert "INSERT INTO moneyflow" in sql
        assert "buy_sm_vol" in sql and "note_txt" in sql
        assert "unlisted_junk" not in sql, "源帧多余列不得入库（列集=DB 行声明）"
        (row,) = cur.last_batch
        # 列序 = PK + all_cols：ts_code, trade_date, buy_sm_vol, note_txt
        assert row[0] == "000001.SZ" and row[1] == "20260930"
        assert row[2] == 123.5 and isinstance(row[2], float), "float 列走 _safe_float 数值化"
        assert row[3] == "x"

    def test_changed_db_row_changes_write_shape(self):
        """反证（单测面）：DB 行改成不同列 ⇒ 写入形状跟着变（证明真读 DB，非残留常量）。"""
        cur, _ = self._run(["net_mf_vol"], ["note_txt"])
        sql = cur.last_sql
        assert "net_mf_vol" in sql and "buy_sm_vol" not in sql
        (row,) = cur.last_batch
        assert row[2] == -7.0, "float 归一取的是 DB 行新列 net_mf_vol"

    def test_missing_placement_row_raises(self):
        """归置行缺失 ⇒ RuntimeError（fail-loud，不静默退空列写裸主键行）。"""
        h = engine._make_tier1_handler(
            "featured_daily", "moneyflow", "moneyflow", ["ts_code", "trade_date"],
            sync_id="moneyflow_sync", cols_from_sync_kind=True)
        cur = _FakeCursor([])   # fetchone → None ⇒ {}
        with patch.object(engine, "get_conn", return_value=_FakeConn(cur)), \
             pytest.raises(RuntimeError, match="sync_kind_config 无归置行"):
            h({"id": "moneyflow_sync", "provider": "tushare"}, "20260930")

    def test_cols_resolved_per_call_not_at_import(self):
        """import 零触库钉：`_make_tier1_handler` 工厂调用本身不查库（解析在每轮 handler 调用）。"""
        with patch.object(engine, "get_conn", side_effect=AssertionError("import 期触库")):
            h = engine._make_tier1_handler(
                "featured_daily", "moneyflow", "moneyflow", ["ts_code", "trade_date"],
                sync_id="moneyflow_sync", cols_from_sync_kind=True)
        assert callable(h)


class TestRegistryWiring:
    def test_all_registered_tier1_use_sync_kind_source(self):
        """注册项（_TIER1_BATCH 8 项）全部 cols_from_sync_kind=True——防新增项漏旗。"""
        src = inspect.getsource(engine)
        reg = src.split("for _sid, (_kind, _sub, _tbl, _pk) in _TIER1_BATCH.items():")[1]
        reg = reg.split("# 全量重建的")[0]
        assert "cols_from_sync_kind=True" in reg
        assert "float_cols=" not in reg and "text_cols=" not in reg, \
            "注册处不得再传列字面量（单源化）"

    def test_engine_source_has_no_col_constants(self):
        """grep 钉：engine.py 无 `_TIER1_FLOAT_COLS`/`_TIER1_TEXT_COLS` 残留。"""
        src = inspect.getsource(engine)
        assert "_TIER1_FLOAT_COLS" not in src
        assert "_TIER1_TEXT_COLS" not in src

    def test_full_rebuild_factory_has_no_text_cols_param(self):
        """批 115：`_make_full_rebuild_handler` 死参数 text_cols 已退役（签名级）。"""
        sig = inspect.signature(engine._make_full_rebuild_handler)
        assert "text_cols" not in sig.parameters

    def test_read_sync_kind_returns_col_arrays(self):
        """`_read_sync_kind` 返回体带 float_cols/text_cols（消费契约钉）。"""
        cur = _FakeCursor([_placement_row(["a"], ["b"])])
        with patch.object(engine, "get_conn", return_value=_FakeConn(cur)):
            row = engine._read_sync_kind("moneyflow_sync")
        assert row["float_cols"] == ["a"] and row["text_cols"] == ["b"]
