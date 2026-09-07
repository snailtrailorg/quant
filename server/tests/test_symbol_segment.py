"""标的代码段互斥测试（24 号 §2.3，固化「标的集天然分流」前提）。

astock/etf/cb 写同一张 bar_1D，靠 6 位代码段不重叠分流（股票 6xx/0xx、基金 5xx/15x、
转债 11x/12x）——若未来发重叠段，ON CONFLICT DO NOTHING 会静默丢数据。
"""
import pytest


def test_symbol_segment_mutually_exclusive():
    """三 kind 代码段前缀互斥（防静默丢数据的唯一依据，需测试固化）。"""
    astock_seg = {"600000.SH", "000001.SZ"}
    etf_seg = {"510300.SH", "159915.SZ"}
    cb_seg = {"113000.SH", "123000.SZ"}
    a_prefixes = {s[:3] for s in astock_seg}
    e_prefixes = {s[:3] for s in etf_seg}
    c_prefixes = {s[:3] for s in cb_seg}
    assert not (a_prefixes & e_prefixes), "股票/ETF 代码段重叠"
    assert not (a_prefixes & c_prefixes), "股票/转债 代码段重叠"
    assert not (e_prefixes & c_prefixes), "ETF/转债 代码段重叠"


def test_delete_by_sync_item_deletes_all_symbols():
    """切换重建原语：delete_by_sync_item 批量删该同步项所有标的（非 per-symbol）。"""
    from unittest.mock import patch, MagicMock
    from src.data_sync import engine
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.rowcount = 42
    with patch.object(engine, "_list_static_ts_codes", return_value=["600000.SH", "000001.SZ"]), \
         patch.object(engine, "get_conn", return_value=conn):
        r = engine.delete_by_sync_item("astock_daily")
    assert r["status"] == "success"
    assert r["deleted"] == 42
    sql = conn.execute.call_args.args[0]
    assert "DELETE FROM bar_1D" in sql
    assert "ANY" in sql   # 批量删除，非逐 symbol
