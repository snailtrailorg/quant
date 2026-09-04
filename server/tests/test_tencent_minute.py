"""腾讯分钟攒测试（分钟数据源重构 21 号 §3.2）。"""
from unittest.mock import MagicMock, patch

import pytest


def test_to_rows_ts_mapping():
    """字段映射：中间段直接对齐 + 收盘竞价 1500→1501 + 开盘竞价 0930 丢弃 + 手×100。"""
    from src.data_sync.tencent_minute import _to_rows
    bars = [
        ("20260904", "0930", 9.27, 9.27, 9.27, 9.27, 185100.0),  # 开盘竞价，丢弃
        ("20260904", "0931", 9.27, 9.30, 9.27, 9.30, 1353200.0),  # 常规
        ("20260904", "1500", 9.42, 9.43, 9.42, 9.43, 964000.0),   # 收盘竞价
    ]
    rows = _to_rows("600000.SHSE", bars)
    assert len(rows) == 2                       # 0930 丢弃
    # 0931 直接对齐（分钟末）
    assert rows[0][0] == "600000.SHSE"
    assert rows[0][1] == "1min"
    assert (rows[0][2].hour, rows[0][2].minute) == (9, 31)
    assert rows[0][3:7] == (9.27, 9.30, 9.27, 9.30)   # open/high/low/close
    assert rows[0][7] == 1353200.0
    assert rows[0][8] == 0.0 and rows[0][9] is None and rows[0][10] == "tencent"   # amount=0(NOT NULL)/adj NULL, source
    # 1500 → 1501（收盘竞价错位）
    assert (rows[1][2].hour, rows[1][2].minute) == (15, 1)


def test_parse_tencent_field_order():
    """字段顺序 [开收高低]（close 在 high/low 前）+ 手×100。"""
    from src.data_sync.tencent_minute import _parse_tencent
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"data": {"sh600000": {"m1": [
        ["202609041458", "9.20", "9.25", "9.26", "9.19", "100.00", {}, "0.01"],
    ]}}}
    with patch("src.data_sync.tencent_minute.requests.get", return_value=fake_resp):
        bars = _parse_tencent("600000.SH")
    # (date, hhmm, open, high, low, close, vol) = ("20260904","1458", 9.20, 9.26, 9.19, 9.25, 10000)
    assert bars[0] == ("20260904", "1458", 9.20, 9.26, 9.19, 9.25, 10000.0)


def test_sync_disabled_when_not_tencent():
    """数据源开关非 tencent → disabled（互斥）。"""
    from src.data_sync import tencent_minute as tm
    with patch.object(tm, "_data_source", return_value="tushare"):
        assert tm.sync_tencent_minute()["status"] == "disabled"


def test_sync_saves_bars():
    """主流程：扫描展开表 + 拉取 + save_bars('1min') + 游标推进。"""
    from src.data_sync import tencent_minute as tm
    bars = [("20260904", "0931", 9.27, 9.30, 9.27, 9.30, 1353200.0)]
    lock = MagicMock(); lock.acquired = True; lock.__enter__.return_value = lock
    with patch.object(tm, "_data_source", return_value="tencent"), \
         patch.object(tm._pdb, "is_trading_day", return_value=True), \
         patch.object(tm, "SyncLock", return_value=lock), \
         patch.object(tm, "_minute_symbols", return_value=["600000.SHSE"]), \
         patch.object(tm, "_parse_tencent", return_value=bars), \
         patch.object(tm._pdb, "save_bars", return_value=1) as save_mock, \
         patch.object(tm, "_check_gap"), \
         patch.object(tm, "_log_sync"):
        r = tm.sync_tencent_minute()
    assert r["status"] == "done"
    save_mock.assert_called_once()
    assert save_mock.call_args.args[0] == "1min"   # freq
    assert save_mock.call_args.args[1][0][10] == "tencent"


def test_check_gap_notifies_when_stale():
    """漏取检测：MAX(ts) 落后昨天 → safe_notify 告警。"""
    import importlib
    from datetime import date, datetime, timedelta
    from src.data_sync import tencent_minute as tm
    notify_mod = importlib.import_module("src.alert_notify.notify")
    conn = MagicMock(); conn.__enter__.return_value = conn
    # 两次 execute 返回不同值：MAX(ts) 落后 3 天 / 上一交易日 = 昨天
    conn.execute.return_value.fetchone.side_effect = [
        (datetime.now() - timedelta(days=3),),   # bar_1min MAX(ts)
        (date.today() - timedelta(days=1),),     # trade_cal 上一交易日
    ]
    with patch.object(tm._pdb, "get_conn", return_value=conn), \
         patch.object(tm._pdb, "is_trading_day", return_value=True), \
         patch.object(notify_mod, "safe_notify") as notify_mock:
        tm._check_gap("600000.SHSE")
    notify_mock.assert_called_once()
    assert "漏取" in notify_mock.call_args.args[1]
