"""批189-M2 纯函数单测（不连库）。"""
import pytest

from src.data_platform import iface_store as S


def test_code_regex_and_prefix():
    S.check_code("md", "md-xtp")
    S.check_code("td", "td-xtp-guard")
    for bad in ["MD-XTP", "md_xtp", "-md", "1md", "x" * 40]:
        with pytest.raises(S.IfaceError):
            S.check_code("md", bad)
    with pytest.raises(S.IfaceError):
        S.check_code("md", "td-xtp")


def test_login_key_rules():
    assert S._login_key("md", "xtp", "A1", "enc") == "xtp|A1"
    assert S._login_key("md", "binance_perp", None, None) == "binance_perp|public"
    assert S._login_key("td", "xtp", None, "enc") == "xtp|legacy"
    assert S._login_key("td", "paper", None, None, True) == "paper|virtual"


def test_unknown_kind():
    with pytest.raises(S.IfaceError) as e:
        S._tbl("xx")
    assert e.value.code == "KIND_UNKNOWN"
