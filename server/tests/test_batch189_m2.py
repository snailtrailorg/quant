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

def test_read_projection_masks_credentials():
    proj, keys = S._read_projection(("name", "credentials_encrypted", "enabled"))
    assert "credentials_encrypted IS NOT NULL AS has_credentials" in proj
    assert "has_credentials" in keys and "credentials_encrypted" not in keys


def test_enable_requires_account_key():
    ok = lambda role, prov: True
    row = {"provider": "xtp", "credentials_encrypted": "enc", "login_key": "xtp|legacy", "account_key": None}
    with pytest.raises(S.IfaceError) as e:
        S.validate_payload("td", row, ok)
    assert e.value.code == "CREDENTIALS_INCOMPLETE"
    # 公开行情（md 且无凭证）允许
    S.validate_payload("md", {"provider": "binance_perp", "credentials_encrypted": None,
                              "login_key": "binance_perp|public", "account_key": None}, ok)
