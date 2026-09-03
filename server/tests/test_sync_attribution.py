"""限流熔断归因拆分单测（2026-09-03）。

锁住：sync_all/_sync_astock_minute 的 DB 写不再计入 Tushare 熔断——
只有 Tushare API 调用失败才计熔断；API 失败同时从「吞成 empty 假装成功」改为
「记 failed + 计熔断」。归因拆分根治点：熔断上下文只包 Tushare 拉取、DB 写在外。
"""
import pandas as pd
from unittest.mock import patch, MagicMock

import pytest


def _fake_df(ts_code="600000.SH", n=1):
    """假日线/分钟线 df（含 _daily_to_rows 所需列）。"""
    return pd.DataFrame([{
        "ts_code": ts_code,
        "trade_time": "2026-08-08 09:31:00",
        "trade_date": "20260808",
        "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
        "vol": 1000.0, "amount": 10000.0, "adj_factor": None,
    } for _ in range(n)])


@pytest.fixture(autouse=True)
def _clean():
    from src.data_platform import rate_limit
    rate_limit.reset_registries()
    yield
    rate_limit.reset_registries()


def _breaker_fails() -> int:
    """Tushare 熔断器当前连续失败计数（无则 0）。"""
    from src.data_platform import rate_limit
    br = rate_limit._BREAKERS.get("tushare")
    return br._fails if br else 0


# --- _sync_astock_minute 归因 ---

def test_minute_api_error_counts_breaker():
    """API 拉取失败 → 计熔断 + 记 failed（回归锁）。"""
    from src.data_sync import engine
    cfg = {"id": "astock_minute", "last_sync_date": "20260807", "enabled": True}
    with patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH"]), \
         patch("src.data_platform.adapters.tushare_adapter.pull_minute",
               side_effect=Exception("tushare down")):
        r = engine._sync_astock_minute(cfg, "20260808")
    assert len(r["failed_dates"]) == 1
    assert _breaker_fails() == 1


def test_minute_db_error_not_counts_breaker():
    """DB 写失败 → 记 failed 但不计熔断（归因拆分核心）。"""
    from src.data_sync import engine
    cfg = {"id": "astock_minute", "last_sync_date": "20260807", "enabled": True}
    with patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH"]), \
         patch("src.data_platform.adapters.tushare_adapter.pull_minute", return_value=_fake_df()), \
         patch("src.data_platform.db.save_bars", side_effect=Exception("db down")):
        r = engine._sync_astock_minute(cfg, "20260808")
    assert len(r["failed_dates"]) == 1
    assert _breaker_fails() == 0


# --- sync_all 归因 ---

def test_sync_all_api_error_counts_breaker():
    """sync_all 日线：API 失败 → 计熔断 + 记 failed（原吞成 empty 假装成功）。"""
    from src.data_sync import engine
    with patch("src.data_sync.engine._get_pro_api",
               return_value=(MagicMock(), MagicMock(), "astock", "1D", "daily")), \
         patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH"]), \
         patch("src.data_sync.engine._get_list_date", return_value="20260101"), \
         patch("src.data_sync.engine._pull_daily_df", side_effect=Exception("tushare down")):
        r = engine.sync_all("astock_daily")
    assert r["failed_count"] == 1
    assert _breaker_fails() == 1


def test_sync_all_db_error_not_counts_breaker():
    """sync_all 日线：DB 写失败 → 记 failed 但不计熔断。"""
    from src.data_sync import engine
    with patch("src.data_sync.engine._get_pro_api",
               return_value=(MagicMock(), MagicMock(), "astock", "1D", "daily")), \
         patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH"]), \
         patch("src.data_sync.engine._get_list_date", return_value="20260101"), \
         patch("src.data_sync.engine._pull_daily_df", return_value=_fake_df()), \
         patch("src.data_sync.engine._save_bars", side_effect=Exception("db down")):
        r = engine.sync_all("astock_daily")
    assert r["failed_count"] == 1
    assert _breaker_fails() == 0


def test_sync_all_success_saves_and_counts_ok():
    """sync_all 日线：成功路径拉取+入库，ok 计数正确（归因拆分不破坏正常流）。"""
    from src.data_sync import engine
    with patch("src.data_sync.engine._get_pro_api",
               return_value=(MagicMock(), MagicMock(), "astock", "1D", "daily")), \
         patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH"]), \
         patch("src.data_sync.engine._get_list_date", return_value="20260101"), \
         patch("src.data_sync.engine._pull_daily_df", return_value=_fake_df()), \
         patch("src.data_sync.engine._save_bars", return_value=1) as msave:
        r = engine.sync_all("astock_daily")
    assert r["status"] == "success"
    assert r["ok"] == 1
    assert r["saved"] == 1
    msave.assert_called_once()
