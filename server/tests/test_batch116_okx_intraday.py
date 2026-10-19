"""批 116：OKX 永续盘中 bar（hourly/1min/15min）——101b 范式 + OKX 特有差异面。

覆盖六组：①bar 词映射单源（大小写敏感/非内部 freq 拒绝/请求携带映射词）
②契约与注册（capabilities/decls/SPECS⊆HANDLERS/CAP_MAP/RECONCILE_SCOPE）
③handler 工厂游标契约（retention 下界/cursor_upto=实取末日/skipped 语义）
④限频实例级声明 ⑤真库对账（0159 三行↔SPECS 逐项/manual 钉/retention 非空）
⑥重试预算常量钉（详细断言在 102b 同批更新）。
离线为默认；真库对账 skipif 自动跳过。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from src.data_platform.adapters import okx_adapter as OA


class TestBarWordMapping:
    def test_internal_freq_maps_to_okx_bar(self):
        assert OA._INTRADAY_BAR_MAP == {"1h": "1H", "1min": "1m", "15min": "15m"}

    @pytest.mark.parametrize("freq", ["1H", "1m", "15m", "1D", "1Dutc", "5min", ""])
    def test_pull_minute_rejects_non_internal_freq(self, freq):
        ad = OA.OkxAdapter()
        with pytest.raises(ValueError, match="非内部 freq"):
            ad.pull_minute("BTC-USDT-SWAP", freq, "20261018", "20261019")

    def test_request_carries_mapped_bar_word(self):
        """捕获 params['bar']——OKX 大小写敏感（拒 1h 形态），映射错=空数据。"""
        ad = OA.OkxAdapter()
        captured = []

        def _fake_request(path, params):
            captured.append(dict(params))
            if len(captured) == 1:
                ts = str(int(datetime(2026, 10, 19, tzinfo=timezone.utc).timestamp() * 1000))
                return {"data": [[ts, "1", "2", "0.5", "1.5", "10", "20", "30", "1"]]}
            return {"data": []}

        ad._request = _fake_request
        df = ad.pull_minute("BTC-USDT-SWAP", "1h", "20261018", "20261019")
        assert captured[0]["bar"] == "1H", captured
        assert len(df) == 1
        # utc 变体裁定钉守：盘中档不加 utc 族（整点对齐无日界歧义——与日线 1Dutc 理由不同层）
        assert all("utc" not in v for v in OA._INTRADAY_BAR_MAP.values())


class TestCapabilityAndRegistry:
    def test_capabilities_include_intraday(self):
        assert OA.OkxAdapter.capabilities >= {"okx_perp_hourly", "okx_perp_1min", "okx_perp_15min"}

    def test_decls_have_bar_minute_perp(self):
        kinds = {(d.kind, d.temporality) for d in OA.OkxAdapter.capability_decls}
        assert ("bar_minute", "historical") in kinds
        from src.quant_common.contract import validate_capability_decls
        validate_capability_decls(OA.OkxAdapter.capability_decls)

    def test_specs_keys_are_registered_handlers(self):
        from src.data_sync import engine
        assert set(engine._OKX_BAR_SPECS) <= set(engine._HANDLERS)

    def test_cap_map_covers_new_ids(self):
        from src.quant_common.markets import SYNC_ID_CAP_MAP
        for sid in ("okx_perp_hourly", "okx_perp_1min", "okx_perp_15min"):
            assert SYNC_ID_CAP_MAP.get(sid) == "hist_quote", sid

    def test_reconcile_scope_covers_new_ids(self):
        from src.data_sync import engine
        for sid in engine._OKX_BAR_SPECS:
            assert sid in engine._RECONCILE_SYMBOL_SCOPE, sid


class _FakeOkx:
    venue = "OKX"

    def __init__(self, symbols=("BTC-USDT-SWAP",), day=None, empty=False):
        self._symbols = list(symbols)
        self._day = day
        self._empty = empty
        self.calls = []

    def list_symbols(self, refresh=False):
        return list(self._symbols)

    def symbol_inception(self, symbol):
        return None

    def available_range(self, kind):
        return ("2020-01-01", None)

    def publish_lag(self, kind):
        return 1

    def fetch_supply(self, kind, sub_kind=None, **p):
        self.calls.append({"kind": kind, "sub_kind": sub_kind, **p})
        if self._empty:
            return pd.DataFrame()
        return pd.DataFrame([{"okx_symbol": p["symbol"], "ts": 0}])

    def to_bar_rows(self, df, freq, adj_map=None):
        if df is None or df.empty:
            return []
        sym = str(df["okx_symbol"].iloc[0])
        ts = datetime(self._day.year, self._day.month, self._day.day, tzinfo=timezone.utc)
        return [(sym + ".OKX", freq, ts, 1.0, 2.0, 0.5, 1.5, 10.0, 20.0, None, "okx")]


def _run(sync_id, adapter, cfg=None, end_date="20260131"):
    from src.data_sync import engine
    cfg = cfg or {"id": sync_id, "provider": "okx", "last_sync_date": None,
                  "retention": date(2026, 1, 25)}
    with patch.object(engine, "_get_supply_adapter", return_value=adapter), \
         patch("src.data_platform.db.save_bars", return_value=1), \
         patch("src.data_platform.db.save_bars_overwrite", return_value=1):
        return engine._HANDLERS[sync_id](cfg, end_date, None)


class TestHandlerFactory:
    def test_fetch_uses_kind_subkind_and_freq(self):
        ad = _FakeOkx(day=date(2026, 1, 20))
        _run("okx_perp_hourly", ad)
        c = ad.calls[0]
        assert (c["kind"], c["sub_kind"], c["freq"]) == ("bar_minute", "perp", "1h")
        assert c["start"] == "20260125" and c["end"] == "20260131"

    def test_cursor_is_last_day_with_data(self):
        ad = _FakeOkx(day=date(2026, 1, 29))
        r = _run("okx_perp_1min", ad)
        assert r["cursor_upto"] == "20260129", r

    def test_unpublished_tail_is_skipped(self):
        from src.data_sync import engine
        today = datetime.now(timezone.utc).date()
        ad = _FakeOkx(empty=True)
        cfg = {"id": "okx_perp_15min", "provider": "okx", "last_sync_date": None,
               "retention": today - timedelta(days=1)}
        end = (today - timedelta(days=1)).strftime("%Y%m%d")
        with patch.object(engine, "_get_supply_adapter", return_value=ad), \
             patch("src.data_platform.db.save_bars", return_value=0):
            r = engine._HANDLERS["okx_perp_15min"](cfg, end, None)
        assert r["pulled"] == 0 and r["log_status"] == "skipped"


class TestRateLimitShared:
    def test_same_instance_single_window(self):
        ad = OA.OkxAdapter()
        assert hasattr(ad, "_req_times")


_HAS_DB = True
try:
    from src.data_platform.db import get_conn
    with get_conn() as _c:
        _c.execute("SELECT 1 FROM sync_config LIMIT 1")
except Exception:
    _HAS_DB = False


@pytest.mark.skipif(not _HAS_DB, reason="无 dev 库")
class TestDbReconciliation:
    def test_three_rows_match_specs(self):
        from src.data_sync import engine
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, pg_table, sync_mode, schedule, enabled, provider, retention "
                "FROM sync_config WHERE id IN ('okx_perp_hourly','okx_perp_1min','okx_perp_15min')").fetchall()
        by_id = {r[0]: r for r in rows}
        assert len(rows) == 3
        for sid, (kind, sub, freq) in engine._OKX_BAR_SPECS.items():
            r = by_id[sid]
            table = "bar_" + freq.lower()
            assert r[1] == table, (sid, r[1], table)
            assert r[2] == "incremental" and r[4] is True and r[5] == "okx"
            assert r[3] == "manual", sid + " schedule=manual 是存储+代理双闸门，cron 化须用户裁定"
            assert r[6] is not None, sid + " retention 为空=错列（start_floor 已死列）"

    def test_kind_config_rows(self):
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sync_id, kind, sub_kind, pg_table FROM sync_kind_config "
                "WHERE sync_id IN ('okx_perp_hourly','okx_perp_1min','okx_perp_15min')").fetchall()
        assert len(rows) == 3
        for r in rows:
            assert (r[1], r[2]) == ("bar_minute", "perp")


class TestRetryBudget:
    def test_max_retry_is_five(self):
        assert OA._MAX_RETRY == 5, "批 116 v3：3→5（预算不够，非异常未捕）"
