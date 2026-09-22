"""批 64：hub 对账接口行 provider 切换测试钉（_poll_iface_switch 模块级纯函数）。"""
from unittest.mock import MagicMock, patch


def _r(version):
    r = MagicMock()
    r.get.return_value = version
    return r


def _iface(provider):
    return {"provider": provider, "credentials": {}, "params": {},
            "market": "astock", "capabilities": ["trading"]}


def test_first_poll_baseline():
    """首次 poll（prev=None）建基线，不判切换。"""
    from src.md_hub.main import _poll_iface_switch
    v, switched = _poll_iface_switch(_r("5"), None, None, "xtp")
    assert v == "5" and switched is False


def test_no_version_change():
    """版本无变化 → 不重读接口行、不切换。"""
    from src.md_hub.main import _poll_iface_switch
    v, switched = _poll_iface_switch(_r("5"), "5", None, "xtp")
    assert v == "5" and switched is False


def test_version_change_provider_switched():
    """版本变化 + provider 变化 → 切换 True。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row", return_value=_iface("emt_emq")):
        v, switched = _poll_iface_switch(_r("6"), "5", None, "xtp")
    assert v == "6" and switched is True


def test_version_change_provider_same():
    """版本变化但 provider 不变（改 params/凭证/position 非首行）→ 不切换。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row", return_value=_iface("xtp")):
        v, switched = _poll_iface_switch(_r("6"), "5", None, "xtp")
    assert v == "6" and switched is False


def test_valkey_fail_keeps_prev():
    """Valkey 读失败 → 返回原版本、不切换（下轮再试）。"""
    from src.md_hub.main import _poll_iface_switch
    r = MagicMock()
    r.get.side_effect = Exception("down")
    v, switched = _poll_iface_switch(r, "5", None, "xtp")
    assert v == "5" and switched is False


def test_get_iface_fail_updates_version_no_switch():
    """接口行读失败（DB/行被删）→ 更新版本但不切换。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row", side_effect=Exception("db down")):
        v, switched = _poll_iface_switch(_r("6"), "5", None, "xtp")
    assert v == "6" and switched is False
