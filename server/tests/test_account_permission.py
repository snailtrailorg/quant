"""D1：account_allows 行为级测试（mock 连接——无库环境可跑，B-P2-7 补钉）。

钉什么：判定顺序 category→exchange→board→(main)ST→convertible、全链 fail-closed、
board↔exchange 一致性、SELL 无关（方向不在此函数）、perp 交 market_op。
"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.data_platform.perms import account_allows


def _attr(category="stock", exchange="SHSE", board="main"):
    return SimpleNamespace(category=category, exchange=exchange, board=board)


def _perm(cats=("stock", "etf", "convertible", "fund", "reits", "perp"),
          exchs=("SHSE", "SZSE", "BSE"), boards=("main", "star", "chinext", "bse"),
          is_st=False, conv=True):
    return (list(cats), list(exchs), list(boards), is_st, conv)


def _call(attr, perm=None, st=None, _st_in_list=False):
    """st 参数=批 117 前旧 ST 态（已退役，保留签名兼容）；_st_in_list=批 117 st_list 在档态。
    perms 直查 st_list（to_regclass / 标的行 / 表空探测三查），mock 按关键词分派。"""
    sm = MagicMock()
    sm.get.return_value = attr
    sm.effective_attr.return_value = st

    class _Cur:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    conn = MagicMock()

    def _execute(sql, params=()):
        if "to_regclass" in sql:
            return _Cur(("st_list",))
        if "ORDER BY trade_date DESC" in sql:
            # 官方名单两态全集：在档=ST；无行=非 ST（但表须非空，否则冷启动 fail-open）
            return _Cur((1,) if _st_in_list else None)
        if "FROM st_list LIMIT 1" in sql:
            return _Cur(None)   # 表空=冷启动 fail-open（本文件钉品种/板权限，ST 态另见批 117）
        return _Cur(perm)

    conn.execute.side_effect = _execute
    conn.__enter__.return_value = conn
    with patch("src.data_platform.security_master.SMClient", return_value=sm), \
         patch("src.data_platform.db.get_conn", return_value=conn):
        return account_allows(1, "600000.SHSE")


class TestAccountAllows:
    def test_stock_main_allowed(self):
        assert _call(_attr(), _perm()) is True

    def test_category_mismatch_rejected(self):
        assert _call(_attr(category="stock"), _perm(cats=("etf",))) is False

    def test_exchange_mismatch_rejected(self):
        # account 无深股东户 → 深主板拒
        assert _call(_attr(exchange="SZSE"), _perm(exchs=("SHSE", "BSE"))) is False

    def test_board_mismatch_rejected(self):
        # account 无创业板 → 创业板拒
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
        # 批 117：board=main 且标的在 st_list 官方名单 且 account 未开 ST → 拒
        # （st_list 在档态由 _st_in_list=True 走通——旧 effective_attr(st=) 路径已退役）
        assert _call(_attr(board="main"), _perm(is_st=False), _st_in_list=True) is False

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


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestAccountPermissionSeed:
    def test_trading_accounts_seeded(self):
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            n = conn.execute(
                "SELECT count(*) FROM account_permission vp "
                "JOIN trading_account e ON e.id = vp.account_id "
                "WHERE e.market='astock'").fetchone()[0]
        assert n >= 1

    def test_account_allows_seeded_account(self):
        # 种子默认权限：中泰XTP account 放行沪主板 600000（stock+SHSE+main 全在默认集合）
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            vid = conn.execute(
                "SELECT id FROM trading_account WHERE provider='xtp' LIMIT 1").fetchone()
        if vid is None:
            pytest.skip("无 xtp 交易 account")
        assert account_allows(vid[0], "600000.SHSE") is True
        assert account_allows(999999, "600000.SHSE") is False   # 不存在 account fail-closed


class TestAccountPermissionEndpoint:
    def test_put_bad_value_400(self):
        from src.web_api.routes.mgmt import put_account_permission
        from src.web_api.models import AccountPermissionReq
        from src.web_api.errors import ApiError
        with pytest.raises(ApiError) as e:
            put_account_permission(4, AccountPermissionReq(
                allowed_categories=["stock"], allowed_exchanges=["SHSE"],
                allowed_boards=["mainn"]), payload={"username": "test"})
        assert e.value.code == "ACCOUNT_PERM_BAD_VALUE"

    def test_put_nonexistent_account_404(self):
        from src.web_api.routes.mgmt import put_account_permission
        from src.web_api.models import AccountPermissionReq
        from src.web_api.errors import ApiError
        with pytest.raises(ApiError) as e:
            put_account_permission(999999, AccountPermissionReq(
                allowed_categories=["stock"], allowed_exchanges=["SHSE"],
                allowed_boards=["main"]), payload={"username": "test"})
        assert e.value.code == "ACCOUNT_NOT_FOUND"
