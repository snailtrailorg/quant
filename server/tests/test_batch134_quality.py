"""批 134：质量校验语义修正 + 全量重建聚合（数据正确性级）。

背景：全量重建逐标的逐 issue warning ⇒ 实测 16 万行/小时挤爆 journald；且真噪声源是**时序断点**
（df 级启发式把春节/国庆长假当断点：300/300 标的中标、34.5 处/标的、96% 落在长假），不是 vol=0/跳空。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "pre_close"]


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


def _row(code="000001.SZ", d="20260925", o=10, h=10.2, l=9.9, c=10.1, v=1000, pc=10.0):
    return (code, d, o, h, l, c, v, pc)


class TestSemantics:
    def test_suspended_flat_is_not_issue(self):
        """停牌：vol=0 且四价齐平 ⇒ 只进 suspended 桶，不产 issue。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([_row(o=10, h=10, l=10, c=10, v=0)]))
        assert q["issues"] == [], q["issues"]
        assert q["counts"]["suspended"] == 1

    def test_limit_board_vol_positive_is_not_issue(self):
        """一字板（vol>0 且 O=H=L=C）是正常行情，不是停牌、不产 issue。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([_row(o=10, h=10, l=10, c=10, v=500)]))
        assert q["issues"] == []
        assert q["counts"]["suspended"] == 0

    def test_suspended_residual_is_issue(self):
        """vol=0 但价格不齐平 ⇒ 真异常（残差桶），必须报。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([_row(o=15, h=15, l=15, c=16, v=0)]))
        assert any("成交量为 0" in i for i in q["issues"]), q["issues"]
        assert q["counts"]["suspended_residual"] == 1

    def test_zero_price_still_reported(self):
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([_row(o=0, h=0, l=0, c=0, v=100)]))
        assert any("为 0" in i for i in q["issues"])
        assert q["counts"]["zero_price"] == 1

    def test_holiday_gap_is_not_issue(self):
        """长假（8 自然日、交易日连续）不再被当断点——这是 v1 的真噪声源。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([_row(d="20260210"), _row(d="20260218")]))
        assert not any("时序断点" in i for i in q["issues"]), q["issues"]

    def test_real_gap_not_issue_anymore(self):
        """真断点（隔 8 个月）也不由本函数判——属 engine._find_gaps（日历感知）。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([_row(d="20260101"), _row(d="20260925")]))
        assert not any("时序断点" in i for i in q["issues"])

    def test_big_gap_not_issue_anymore(self):
        """跳空 >20%（除权伪命中为主）已删。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([_row(c=20, pc=10.0)]))
        assert not any("跳空" in i for i in q["issues"])

    def test_empty_df_returns_all_count_keys(self):
        """空 df 也必须返回四键（累加侧不得 KeyError）。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(pd.DataFrame(columns=COLS))
        assert q["counts"] == {"dup": 0, "zero_price": 0, "suspended": 0, "suspended_residual": 0}

    def test_valid_keeps_original_semantics(self):
        """valid 仍只看 dup（价 0 / 停牌历史上就不计入 invalid）——改它=静默翻转消费方行为。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        assert validate_bar_quality(_df([_row(o=0, h=0, l=0, c=0, v=100)]))["valid"] is True
        dup = validate_bar_quality(_df([_row(), _row()]))
        assert dup["valid"] is False and dup["counts"]["dup"] == 1


class TestOrdering:
    def test_all_zero_ohlc_suspended_does_not_hit_zero_price(self):
        """顺序钉：全 0 OHLC 的停牌行必须走停牌桶——不能先喷「open 为 0」。

        （真实源里停牌行是 O=H=L=C=前收 而不是 0，但 joinquant 等 provider 共用同一校验器。）
        """
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([_row(o=0, h=0, l=0, c=0, v=0)]))
        assert not any("为 0" in i for i in q["issues"]), q["issues"]
        assert q["counts"]["suspended"] == 1


class TestSummarize:
    def test_single_line_contains_all_fields(self):
        from src.data_platform.adapters.quality import summarize
        msg = summarize("bar_daily", {"dup": 1, "zero_price": 2, "suspended": 30,
                                      "suspended_residual": 3},
                        symbols=800, with_issue=12, errors=1, top=[("600000.SH", 9)])
        assert "\n" not in msg
        for frag in ("覆盖 800", "有问题 12", "重复 1", "价0 2", "停牌 30",
                     "停牌残差 3", "校验器异常 1", "600000.SH(9)"):
            assert frag in msg, (frag, msg)

    def test_add_counts_tolerates_missing_keys(self):
        from src.data_platform.adapters.quality import add_counts, empty_counts
        acc = empty_counts()
        add_counts(acc, None)
        add_counts(acc, {"dup": 2})
        assert acc == {"dup": 2, "zero_price": 0, "suspended": 0, "suspended_residual": 0}


class TestSyncAllAggregation:
    def _adapter(self):
        return SimpleNamespace(provider="tushare", to_bar_rows=lambda df, f, a: [1])

    def test_one_summary_line_for_many_symbols(self, caplog):
        """1000 标的 ⇒ `[quality]` 只 1 条（修复前每标的 0~6 条）。"""
        from src.data_sync import engine
        bad = _df([_row(code="DUP", d="20260925"), _row(code="DUP", d="20260925"),
                   _row(code="SUSP", d="20260925", o=10, h=10, l=10, c=10, v=0)])
        with patch.object(engine, "_get_pro_api", return_value=(self._adapter(), "astock", "1D", "day")), \
                patch.object(engine, "_list_static_ts_codes", return_value=["600000.SH", "000001.SZ"]), \
                patch.object(engine, "_get_list_date", return_value="20200101"), \
                patch.object(engine, "_pull_daily_df", return_value=bad), \
                patch.object(engine, "_save_bars", return_value=1):
            with caplog.at_level(logging.WARNING, logger=engine.__name__):
                out = engine.sync_all("astock_daily")
        lines = [r.message for r in caplog.records if "[quality]" in r.message]
        assert out["ok"] == 2
        assert len(lines) == 1, lines
        assert "覆盖 2 标的" in lines[0] and "重复" in lines[0]

    def test_validator_exception_counted_not_raised(self, caplog):
        """校验器异常走 fail-soft（计入「校验器异常」、不阻入库）。"""
        from src.data_sync import engine
        with patch.object(engine, "_get_pro_api", return_value=(self._adapter(), "astock", "1D", "day")), \
                patch.object(engine, "_list_static_ts_codes", return_value=["600000.SH"]), \
                patch.object(engine, "_get_list_date", return_value="20200101"), \
                patch.object(engine, "_pull_daily_df", return_value=_df([_row()])), \
                patch.object(engine, "_save_bars", return_value=1), \
                patch("src.data_platform.adapters.tushare_adapter.validate_bar_quality",
                      side_effect=RuntimeError("boom")):
            with caplog.at_level(logging.WARNING, logger=engine.__name__):
                out = engine.sync_all("astock_daily")
        assert out["ok"] == 1
        msgs = [r.message for r in caplog.records]
        assert any("校验器异常" in m for m in msgs)
        assert any("校验器异常 1" in m for m in msgs), msgs