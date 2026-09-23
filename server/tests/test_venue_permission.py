"""D1：venue_allows 行为级测试（mock 连接——无库环境可跑，B-P2-7 补钉）。

钉什么：判定顺序 category→exchange→board→(main)ST→convertible、全链 fail-closed、
board↔exchange 一致性、SELL 无关（方向不在此函数）、perp 交 market_op。
"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.data_platform.perms import venue_allows


def _attr(category="stock", exchange="SHSE", board="main"):
    return SimpleNamespace(category=category, exchange=exchange, board=board)


def _perm(cats=("stock", "etf", "convertible", "fund", "reits", "perp"),
          exchs=("SHSE", "SZSE", "BSE"), boards=("main", "star", "chinext", "bse"),
          is_st=False, conv=True):
    return (list(cats), list(exchs), list(boards), is_st, conv)


def _call(attr, perm=None, st=None):
    sm = MagicMock()
    sm.get.return_value = attr
    sm.effective_attr.return_value = st
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = perm
    with patch("src.data_platform.security_master.SMClient", return_value=sm), \
         patch("src.data_platform.db.get_conn", return_value=conn):
        return venue_allows(1, "600000.SHSE")


class TestVenueAllows:
    def test_stock_main_allowed(self):
        assert _call(_attr(), _perm()) is True

    def test_category_mismatch_rejected(self):
        assert _call(_attr(category="stock"), _perm(cats=("etf",))) is False

    def test_exchange_mismatch_rejected(self):
        # venue 无深股东户 → 深主板拒
        assert _call(_attr(exchange="SZSE"), _perm(exchs=("SHSE", "BSE"))) is False

    def test_board_mismatch_rejected(self):
        # venue 无创业板 → 创业板拒
        assert _call(_attr(exchange="SZSE", board="chinext"),
                     _perm(boards=("main", "star", "bse"))) is False

    def test_board_none_fail_closed(self):
        assert _call(_attr(board=None), _perm()) is False

    def test_board_exchange_consistency(self):
        # board=star 必 SHSE、bse 必 BSE（矛盾数据 fail-closed）
        assert _call(_attr(exchange="SZSE", board="star"), _perm()) is False
        assert _call(_attr(exchange="SZSE", board="bse"), _perm()) is False  # bse 却深市
        assert _call(_attr(exchange="BSE", board="bse"), _perm()) is True   # 一致放行

    def test_st_rejected_when_not_allowed(self):
        # board=main 且 is_st=true 且 venue 未开 ST → 拒
        st = {"name": "ST某某", "is_st": True}
        assert _call(_attr(board="main"), _perm(is_st=False), st=st) is False

    def test_st_not_checked_for_non_main(self):
        # 科创/创业 ST 不走 is_st 检查（st 只 board=main 生效）
        st = {"name": "ST科创", "is_st": True}
        assert _call(_attr(exchange="SHSE", board="star"), _perm(is_st=False), st=st) is True

    def test_etf_skips_board_exchange(self):
        # etf 跳过 exchange+board，仅 category 判定
        assert _call(_attr(category="etf", exchange="SHSE", board=None),
                     _perm(exchs=(), boards=())) is True

    def test_convertible_needs_permission(self):
        assert _call(_attr(category="convertible"), _perm(conv=False)) is False
        assert _call(_attr(category="convertible"), _perm(conv=True)) is True

    def test_no_perm_row_fail_closed(self):
        assert _call(_attr(), perm=None) is False

    def test_no_attr_fail_closed(self):
        assert _call(attr=None, perm=_perm()) is False
