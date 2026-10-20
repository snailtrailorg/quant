"""批 131：内部 vt ↔ tushare ts_code 形态（数据正确性级）。"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from src.data_platform.adapters.base import TushareAdapter


def _req(kind, **kw):
    base = dict(kind=kind, symbols=("600000.SHSE",), sub_kind=None, freq="1D",
                range_=(datetime(2026, 9, 1), datetime(2026, 9, 21)),
                mode="consume", temporality="historical", consumer_tag="t",
                preserve_current_normalization=True)
    base.update(kw)
    return SimpleNamespace(**base)


class TestSymbolMatrix:
    def test_vt_to_ts(self):
        ad = TushareAdapter()
        assert ad.to_source_symbol("600000.SHSE") == "600000.SH"
        assert ad.to_source_symbol("000001.SZSE") == "000001.SZ"
        assert ad.to_source_symbol("920196.BSE") == "920196.BJ"

    def test_sse_alias(self):
        assert TushareAdapter().to_source_symbol("600000.SSE") == "600000.SH"

    def test_idempotent_src_format(self):
        ad = TushareAdapter()
        assert ad.to_source_symbol("600000.SH") == "600000.SH"
        assert ad.to_source_symbol("000001.SZ") == "000001.SZ"
        assert ad.to_source_symbol("920196.BJ") == "920196.BJ"

    def test_lowercase_and_space(self):
        assert TushareAdapter().to_source_symbol(" 600000.shse ") == "600000.SH"

    def test_empty_passthrough(self):
        assert TushareAdapter().to_source_symbol("") == ""

    @pytest.mark.parametrize("bad", ["600000", "600000.OF", "X.UNKNOWN", "600000.SZE"])
    def test_unknown_raises(self, bad):
        with pytest.raises(ValueError, match="转换失败"):
            TushareAdapter().to_source_symbol(bad)

class TestPoolEndpointNormalization:
    """审点 7（自补）：`trigger?pool_id=` 端点是本 bug 的唯一生产入口——拆掉 vt_to_ts 即红。"""

    def test_pool_id_symbols_converted(self):
        import src.web_api.routes.sync as S
        captured = {}

        class _Cur:
            def fetchall(self):
                return [("600000.SHSE",), ("000001.SZSE",)]

        class _Conn:
            def execute(self, *a, **k):
                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Task:
            @staticmethod
            def delay(full=False, symbols=None):
                captured["symbols"] = symbols

                class _T:
                    id = "x"
                return _T()

        with patch.object(S, "get_conn", return_value=_Conn()), \
                patch("src.scheduler.tasks.pool_data_sync_task", _Task), \
                patch.object(S, "audit_log"):
            S.trigger_pool_data_api(full=False, pool_id="test-pool", payload={"username": "u"})
        assert captured["symbols"] == ["600000.SH", "000001.SZ"], captured

    def test_pool_family_passes_ts(self):
        import src.data_platform.adapters.base as B
        ad = B.TushareAdapter()
        # pull_pool_table 在 fetch 内惰性 import ⇒ patch 源模块属性（base 模块级无此名）
        with patch("src.data_platform.adapters.tushare_adapter.pull_pool_table",
                   return_value=pd.DataFrame()) as ppt:
            with patch.object(ad, "to_bar_rows", return_value=[]):
                ad.fetch(_req("featured_daily", sub_kind="cyq_chips"))
        assert ppt.call_args[0][1] == "600000.SH", ppt.call_args

    def test_consume_bar_daily_passes_ts(self):
        ad = TushareAdapter()
        with patch.object(ad, "pull_daily", return_value=pd.DataFrame()) as pd_:
            with patch.object(ad, "to_bar_rows", return_value=[]):
                ad.fetch(_req("bar_daily", sub_kind="stock"))
        assert pd_.call_args[0][0] == "600000.SH", pd_.call_args

    def test_bar_minute_passes_ts(self):
        ad = TushareAdapter()
        segs = [("20260901", "20260901")]
        with patch("src.data_platform.adapters.tushare_adapter.split_minute_range",
                   return_value=segs):
            with patch.object(ad, "pull_minute", return_value=pd.DataFrame()) as pm:
                with patch.object(ad, "to_bar_rows", return_value=[]):
                    ad.fetch(_req("bar_minute", freq="1min"))
        assert pm.call_args[0][0] == "600000.SH", pm.call_args

    def test_idempotent_hot_path_still_works(self):
        ad = TushareAdapter()
        with patch.object(ad, "pull_daily", return_value=pd.DataFrame()) as pd_:
            with patch.object(ad, "to_bar_rows", return_value=[]):
                ad.fetch(_req("bar_daily", sub_kind="stock", symbols=("600000.SH",)))
        assert pd_.call_args[0][0] == "600000.SH"


class TestCreatePoolNormalization:
    """P1-2（审员）：create_pool 写侧归一四例——P0-1（vt_to_ts 非校验器）正因此漏网。"""

    def test_invalid_suffix_400(self):
        import src.web_api.routes.backtest as BT
        from src.web_api.errors import ApiError
        captured = {}

        class _Conn:
            def execute(self, *a, **k):
                captured.setdefault("sql", []).append(a[0][:30] if a else "")

                class _C:
                    def fetchone(self):
                        return (1,)
                return _C()

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import types
        req = types.SimpleNamespace(id="p", name="p", category="astock",
                                    description="",
                                    symbolsStr="600000.FOO")
        with patch.object(BT, "get_conn", return_value=_Conn()), \
                patch.object(BT, "audit_log"):
            with pytest.raises(ApiError):
                BT.create_pool(req, payload={"username": "u"})
