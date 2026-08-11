"""分钟线管线单测（A1）：分段 + to_save_rows_min + type 级 handler + per-symbol。

mock Tushare（pull_minute）+ DB（save_bars/get_conn），不连真实服务。
"""
import pandas as pd
from unittest.mock import patch, MagicMock


def _fake_minute_df(ts_code="600000.SH", n=1):
    """假 stk_mins 返回（trade_time 字符串，对齐真实接口）。"""
    return pd.DataFrame([{
        "ts_code": ts_code,
        "trade_time": "2026-08-08 09:31:00",
        "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
        "vol": 1000.0, "amount": 10000.0, "adj_factor": None,
        "trade_date": "20260808",
    } for _ in range(n)])


# --- 分段 ---

def test_split_minute_range_1min():
    """1min: 33 天/段。32 天一段，35+ 天两段，每段不超 33 天。"""
    from src.data_sync.engine import _split_minute_range
    from datetime import datetime as _dt
    assert _split_minute_range("20260101", "20260201", "1min") == [("20260101", "20260201")]
    segs = _split_minute_range("20260101", "20260205", "1min")  # 36 天
    assert len(segs) == 2
    for s, e in segs:
        days = (_dt.strptime(e, "%Y%m%d") - _dt.strptime(s, "%Y%m%d")).days + 1
        assert days <= 33


def test_split_minute_range_5min():
    """5min: 166 天/段，小跨度一段。"""
    from src.data_sync.engine import _split_minute_range
    assert _split_minute_range("20260101", "20260110", "5min") == [("20260101", "20260110")]


# --- to_save_rows_min ---

def test_to_save_rows_min():
    """分钟线 DataFrame -> 行（trade_time 作 ts，vt_symbol 转换）。"""
    from src.data_platform.adapters.tushare_adapter import to_save_rows_min
    rows = to_save_rows_min(_fake_minute_df(), "1min")
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "600000.SHSE"   # vt_symbol
    assert row[1] == "1min"          # freq
    assert row[3] == 10.0            # open
    assert row[6] == 10.1            # close
    assert row[10] == "tushare"      # source


# --- type 级 handler _sync_astock_minute ---

def test_sync_astock_minute_incremental():
    """增量：2 只标的，per-symbol 循环，save_bars 用 1min freq。"""
    from src.data_sync import engine
    cfg = {"id": "astock_minute", "last_sync_date": "20260807", "enabled": True}
    with patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH", "000001.SZ"]), \
         patch("src.data_platform.adapters.tushare_adapter.pull_minute", return_value=_fake_minute_df()), \
         patch("src.data_platform.db.save_bars", return_value=1) as msave:
        r = engine._sync_astock_minute(cfg, "20260808")
    assert r["pulled"] == 2          # 2 只 × 1 行
    assert r["saved"] == 2
    assert msave.call_count == 2
    assert msave.call_args_list[0].args[0] == "1min"   # freq
    assert r["start"] == "20260808"  # last+1


def test_sync_astock_minute_5min_freq():
    """astock_minute_5min 用 5min freq。"""
    from src.data_sync import engine
    cfg = {"id": "astock_minute_5min", "last_sync_date": "20260807", "enabled": True}
    with patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH"]), \
         patch("src.data_platform.adapters.tushare_adapter.pull_minute", return_value=_fake_minute_df()), \
         patch("src.data_platform.db.save_bars", return_value=1) as msave:
        engine._sync_astock_minute(cfg, "20260808")
    assert msave.call_args.args[0] == "5min"


def test_sync_astock_minute_backfill():
    """回补：用 backfill_from，不读 last_sync_date。"""
    from src.data_sync import engine
    cfg = {"id": "astock_minute", "last_sync_date": "20260807", "enabled": True}
    with patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH"]), \
         patch("src.data_platform.adapters.tushare_adapter.pull_minute", return_value=_fake_minute_df()), \
         patch("src.data_platform.db.save_bars", return_value=1):
        r = engine._sync_astock_minute(cfg, "20260808", backfill_from="20260801")
    assert r["start"] == "20260801"


def test_sync_astock_minute_uptodate():
    """start > end_date：返回空，不拉标的。"""
    from src.data_sync import engine
    cfg = {"id": "astock_minute", "last_sync_date": "20260808", "enabled": True}
    with patch("src.data_sync.engine._list_static_ts_codes") as ml:
        r = engine._sync_astock_minute(cfg, "20260808")  # start=20260809 > end
    assert r["pulled"] == 0
    ml.assert_not_called()


def test_sync_astock_minute_failed_symbol():
    """单只失败记 failed_dates，不中断其他标的。"""
    from src.data_sync import engine
    cfg = {"id": "astock_minute", "last_sync_date": "20260807", "enabled": True}

    def _pull(tc, *a, **kw):
        if tc == "000001.SZ":
            raise ValueError("tushare error")
        return _fake_minute_df(tc)
    with patch("src.data_sync.engine._list_static_ts_codes", return_value=["600000.SH", "000001.SZ"]), \
         patch("src.data_platform.adapters.tushare_adapter.pull_minute", side_effect=_pull), \
         patch("src.data_platform.db.save_bars", return_value=1):
        r = engine._sync_astock_minute(cfg, "20260808")
    assert r["pulled"] == 1
    assert len(r["failed_dates"]) == 1
    assert "000001.SZ" in r["failed_dates"][0]


# --- per-symbol ---

def test_sync_symbol_minute_full():
    """空 -> 全量（mode=auto，cnt==0）。"""
    from src.data_sync import engine
    with patch("src.data_sync.engine._get_pro_api",
               return_value=(MagicMock(), MagicMock(), "astock", "1min", "minute")), \
         patch("src.data_sync.engine._local_bar_range", return_value=(None, None, 0)), \
         patch("src.data_sync.engine._get_list_date", return_value="20260101"), \
         patch("src.data_sync.engine._fetch_minute_and_save", return_value=(_fake_minute_df(), 1)) as mf:
        r = engine.sync_symbol("astock_minute", "600000.SH", mode="auto")
    assert r["status"] == "success"
    assert r["mode_used"] == "full"
    mf.assert_called_once()
    assert mf.call_args.args[0] == "600000.SH"
    assert mf.call_args.args[1] == "1min"


def test_sync_symbol_minute_uptodate():
    """有数据无缺口 -> uptodate。"""
    from src.data_sync import engine
    with patch("src.data_sync.engine._get_pro_api",
               return_value=(MagicMock(), MagicMock(), "astock", "1min", "minute")), \
         patch("src.data_sync.engine._local_bar_range", return_value=("20260101", "20260807", 1000)), \
         patch("src.data_sync.engine._find_gaps", return_value=[]):
        r = engine.sync_symbol("astock_minute", "600000.SH", mode="auto")
    assert r["status"] == "uptodate"
    assert r["pulled"] == 0


def test_backfill_symbol_minute():
    """分钟线回补：覆盖写（overwrite=True）。"""
    from src.data_sync import engine
    with patch("src.data_sync.engine._get_pro_api",
               return_value=(MagicMock(), MagicMock(), "astock", "5min", "minute")), \
         patch("src.data_sync.engine._fetch_minute_and_save", return_value=(_fake_minute_df(), 1)) as mf:
        r = engine.backfill_symbol("astock_minute_5min", "600000.SH", "20260101", "20260110")
    assert r["status"] == "success"
    assert r["overwritten"] is True
    assert mf.call_args.args == ("600000.SH", "5min", "20260101", "20260110")
    assert mf.call_args.kwargs == {"overwrite": True}


def test_delete_symbol_minute():
    """删 bar_1min（分钟线表）。"""
    from src.data_sync import engine
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value = MagicMock(rowcount=5)
    with patch("src.data_sync.engine.get_conn", return_value=mock_conn):
        r = engine.delete_symbol("astock_minute", "600000.SH")
    assert r["status"] == "success"
    assert r["deleted"] == 5
    sql = mock_conn.execute.call_args_list[0].args[0]
    assert "DELETE FROM bar_1min" in sql
