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


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as c:
            c.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _db_up(), reason="真库往返（无 dev 库自动跳过）")
def test_td_credentials_encrypted_and_enable_without_reentry():
    """P0 回归：写侧必须加密；启用（不改凭证）不得因合并丢列而 CREDENTIALS_INCOMPLETE。"""
    import uuid
    from src.data_platform import iface_store as S
    code = "td-ut-" + uuid.uuid4().hex[:8]
    ok = lambda role, prov: True
    try:
        S.create_interface("td", {"code": code, "name": "UT", "provider": "xtp", "market": "astock",
                                  "account_key": "UT-" + uuid.uuid4().hex[:8],
                                  "credentials": '{"k":"v"}'}, ok)
        raw = S._get_interface_raw("td", code)
        assert raw["credentials_encrypted"] and raw["credentials_encrypted"] != '{"k":"v"}'
        S.update_interface("td", code, {"enabled": True}, ok)   # 旧实现此处必炸
        row = S.get_interface("td", code)
        assert row["enabled"] is True and row["has_credentials"] is True
        assert row["login_key"].startswith("xtp|")
    finally:
        from src.data_platform.db import get_conn
        with get_conn() as c:
            c.execute("DELETE FROM trade_interface WHERE code=%s", (code,))
            c.commit()


def test_provider_directory_marks_carrier_readiness():
    """目录带就绪态：xtp 交易就绪；emt_emq 交易无端口=待接入（可存草稿不可启用）。"""
    from src.web_api.routes.interfaces_api import _providers
    trade = {x["provider"]: x["ready"] for x in _providers("trade")}
    assert trade["xtp"] is True
    assert trade.get("emt_emq") is False
    assert all(x["ready"] for x in _providers("market"))


def test_iface_status_projection(monkeypatch):
    """状态卡读侧：键在=运行；working/connected 原样投影；缺键不算错。"""
    import src.data_platform.iface_status as S

    class _R:
        def hgetall(self, k):
            return {"working": "1", "connected": "1", "enabled": "1", "provider": "xtp"} \
                if k.endswith("md-ok") else {}
        def ttl(self, k):
            return 60 if k.endswith("md-ok") else -2

    monkeypatch.setattr(S, "business_redis", lambda **kw: _R())
    out = S.status_of("md", ["md-ok", "md-dead"])
    assert out[0]["running"] and out[0]["working"] and out[0]["connected"]
    assert out[0]["provider"] == "xtp"
    assert not out[1]["running"] and out[1]["enabled"] is None


def test_capabilities_must_belong_to_iface_kind():
    """写侧三分类收窄：交易行挂 rt_quote / 行情行挂 trading / 数据源挂行情能力 → 一律拒。"""
    ok = lambda role, prov: True
    base_td = {"provider": "xtp", "credentials_encrypted": "enc",
               "login_key": "xtp|A1", "account_key": "A1"}
    S.validate_payload("td", {**base_td, "capabilities": ["trading"]}, ok)   # 合法
    with pytest.raises(S.IfaceError) as e:
        S.validate_payload("td", {**base_td, "capabilities": ["rt_quote"]}, ok)
    assert e.value.code == "CAPABILITY_INVALID"
    with pytest.raises(S.IfaceError):
        S.validate_payload("md", {"provider": "xtp", "credentials_encrypted": None,
                                  "login_key": "xtp|public", "account_key": None,
                                  "capabilities": ["trading"]}, ok)
    with pytest.raises(S.IfaceError):
        S.validate_payload("ds", {"provider": "tushare", "capabilities": ["rt_quote"]}, ok)


def test_enable_requires_stable_login_key_not_account_key():
    ok = lambda role, prov: True
    # 迁移期行（0192 未回填 account_key）仍可启用——否则停用即不可逆（生产 td-xtp 形态）
    S.validate_payload("td", {"provider": "xtp", "credentials_encrypted": "enc",
                              "login_key": "xtp|legacy", "account_key": None}, ok)
    with pytest.raises(S.IfaceError) as e:
        S.validate_payload("td", {"provider": "xtp", "credentials_encrypted": "enc",
                                  "login_key": "", "account_key": "A1"}, ok)
    assert e.value.code == "CREDENTIALS_INCOMPLETE"
    # 公开行情（md 且无凭证）允许
    S.validate_payload("md", {"provider": "binance_perp", "credentials_encrypted": None,
                              "login_key": "binance_perp|public", "account_key": None}, ok)
