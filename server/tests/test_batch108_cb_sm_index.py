"""批 108 步 4 盲审必修-1 回归：可转债 SM 行的 **索引取值** 钉死。

病根（`盲审批108代码-同判综合.md` 必修-1）：`_sync_cb_basic` 的行元组是 **15 元**
（`r[12]=rate_clause`、`r[13]=list_date`、`r[14]=delist_date`），而 `_sm_upsert` 调用
原写 `r[12], r[13]` ⇒
  - `list_date` 收到 `rate_clause`（`_null_date` 过滤非日期串 → **NULL**，且
    `list_date=COALESCE(...)` 让新行**永远填不回**）；
  - `delist_date` 收到 `list_date` ⇒ 全市场可转债在 SM 里「**上市即退市**」
    （dev 实测 1160/1172 行 `delist_date IS NOT DISTINCT FROM list_date`）。

本测试**不连库**：patch `_sm_upsert` / `_sm_upsert_state` 捕获行，断言取值位。
"""
import os
from unittest.mock import MagicMock

import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_CB_COLS = ("ts_code", "bond_short_name", "stk_code", "stk_short_name", "maturity",
            "par", "issue_price", "conv_price", "conv_start_date", "conv_end_date",
            "maturity_date", "coupon_rate", "rate_clause", "list_date", "delist_date")


def _fake_cb_df():
    """两行：① 转股起止日合法 ② 转股起始日脏（空串）⇒ 走 list_date 回落分支。"""
    return pd.DataFrame([
        {"ts_code": "110102.SH", "bond_short_name": "转债甲", "stk_code": "600000.SH",
         "stk_short_name": "浦发银行", "maturity": 6.0, "par": 100.0, "issue_price": 100.0,
         "conv_price": 12.5, "conv_start_date": "20260805", "conv_end_date": "20320804",
         "maturity_date": "20320804", "coupon_rate": 0.3,
         "rate_clause": "第一年0.3%、第二年0.5%", "list_date": "20260805",
         "delist_date": "20320804"},
        {"ts_code": "113710.SH", "bond_short_name": "转债乙", "stk_code": "601318.SH",
         "stk_short_name": "中国平安", "maturity": 5.0, "par": 100.0, "issue_price": 100.0,
         "conv_price": 30.0, "conv_start_date": "", "conv_end_date": "20310922",
         "maturity_date": "20310922", "coupon_rate": 0.2,
         "rate_clause": "第一年0.2%、第二年0.4%", "list_date": "20260923",
         "delist_date": "20310922"},
    ], columns=list(_CB_COLS))


class _FakeCbAdapter:
    provider = "tushare"
    venue = "SHSE"

    @staticmethod
    def fetch_supply(kind, sub, **kw):
        return _fake_cb_df()


def _run_cb(monkeypatch):
    """跑 `_sync_cb_basic` 并捕获 SM 写侧两路行。"""
    from src.data_sync import engine
    captured = {}
    monkeypatch.setattr(engine, "_get_supply_adapter", lambda cfg: _FakeCbAdapter())
    monkeypatch.setattr(engine, "_get_rate_ds", lambda p: MagicMock())
    monkeypatch.setattr(engine, "get_conn", MagicMock())
    monkeypatch.setattr(engine, "_sm_upsert", lambda rows: captured.update(rows=list(rows)))
    monkeypatch.setattr(engine, "_sm_upsert_state",
                        lambda rows: captured.update(state=list(rows)))
    monkeypatch.setattr("src.data_platform.rate_limit.rate_limit_context", MagicMock())
    engine._sync_cb_basic({"id": "cb_basic", "provider": "tushare"}, "20261006")
    return captured


class TestCbSmIndexMapping:
    def test_list_date_and_delist_date_take_r13_r14(self, monkeypatch):
        cap = _run_cb(monkeypatch)
        rows = {r[0]: r for r in cap["rows"]}
        assert set(rows) == {"110102.SHSE", "113710.SHSE"}, rows

        r = rows["110102.SHSE"]
        assert r[10] == "20260805", f"list_date 应为 r[13]；得到 {r[10]!r}（rate_clause?）"
        assert r[11] == "20320804", f"delist_date 应为 r[14]；得到 {r[11]!r}"
        assert r[12] == engine_session(), "session_id 必须仍为 astock_main"

        r2 = rows["113710.SHSE"]
        assert r2[10] == "20260923" and r2[11] == "20310922"

    def test_not_listed_equals_delisted(self, monkeypatch):
        """核心不变式：`list_date == delist_date` 是**病态**（上市即退市），本仓不得再现。"""
        cap = _run_cb(monkeypatch)
        for r in cap["rows"]:
            assert r[10] != r[11], f"{r[0]} list_date 与 delist_date 相等 ⇒ 索引错配回归"
            assert "年" not in str(r[10]), "list_date 取到 rate_clause 条款文本"

    def test_state_date_fallback_uses_list_date_not_rate_clause(self, monkeypatch):
        """`_sm_upsert_state` 的回落应是 `r[13]`(list_date) 而非 `r[12]`(rate_clause)。

        第 2 行转股起始日为空 ⇒ 应回落到 list_date `2026-09-23`；
        若错取 `r[12]`(rate_clause) 则 `_norm_date` 校验不过、静默退 `2010-01-01`。
        """
        cap = _run_cb(monkeypatch)
        state = {s[0]: s for s in cap["state"]}
        assert state["113710.SHSE"][1] == "2026-09-23", \
            f"回落未取 list_date：{state['113710.SHSE'][1]!r}"


def engine_session():
    from src.data_sync.engine import SM_SESSION_ASTOCK
    return SM_SESSION_ASTOCK
