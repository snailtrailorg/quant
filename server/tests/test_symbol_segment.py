"""标的代码段互斥测试（24 号 §2.3，固化「标的集天然分流」前提）+ 切换重建原语。

astock/etf/cb 写同一张 bar_1D，靠 6 位代码段不重叠分流——若未来发重叠段，
ON CONFLICT DO NOTHING 会静默丢数据。这里枚举真实全段前缀断言互斥（非手挑样例）。
"""

# 真实代码段前缀（沪深京三市场）
ASTOCK_PREFIXES = {
    "600", "601", "603", "605", "688", "689",                          # 沪主板 + 科创板
    "000", "001", "002", "003", "300", "301",                          # 深主板 + 创业板
    "830", "831", "832", "833", "834", "835", "836", "837", "838", "839",  # 北交所
}
ETF_PREFIXES = {
    "510", "511", "512", "513", "515", "516", "517", "518", "520",
    "560", "561", "562", "563", "588",                                # 沪 ETF
    "159", "160", "161", "162",                                        # 深 ETF
}
CB_PREFIXES = {
    "110", "113", "118",                                             # 沪转债
    "123", "127", "128",                                             # 深转债
}


def test_symbol_segment_mutually_exclusive():
    """三 kind 代码段前缀互斥（枚举真实全段，防静默丢数据）。"""
    assert not (ASTOCK_PREFIXES & ETF_PREFIXES), f"股票/ETF 重叠: {ASTOCK_PREFIXES & ETF_PREFIXES}"
    assert not (ASTOCK_PREFIXES & CB_PREFIXES), f"股票/转债 重叠: {ASTOCK_PREFIXES & CB_PREFIXES}"
    assert not (ETF_PREFIXES & CB_PREFIXES), f"ETF/转债 重叠: {ETF_PREFIXES & CB_PREFIXES}"


def test_delete_by_sync_item_deletes_all_symbols():
    """切换重建原语：批量删该同步项所有标的 + 重置游标。"""
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
    delete_sql = conn.execute.call_args_list[0].args[0]
    assert "DELETE FROM bar_1D" in delete_sql
    assert "ANY" in delete_sql
    # 断言 vts 转换（ts_code → vt_symbol）
    vts = conn.execute.call_args_list[0].args[1][0]
    assert set(vts) == {"600000.SHSE", "000001.SZSE"}
    # 断言游标重置 UPDATE
    update_sql = conn.execute.call_args_list[1].args[0]
    assert "UPDATE sync_config" in update_sql
    assert "last_sync_date=NULL" in update_sql


def test_delete_by_sync_item_empty_static_returns_error():
    """静态表空 → 返回 error 防静默假删（盲审 A-P2）。"""
    from unittest.mock import patch
    from src.data_sync import engine
    with patch.object(engine, "_list_static_ts_codes", return_value=[]):
        r = engine.delete_by_sync_item("etf_daily")
    assert r["status"] == "error"
    assert "空" in r["error"]
