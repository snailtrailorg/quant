"""批 64+66a：hub 对账接口行切换测试钉（_poll_iface_switch 模块级纯函数）。

批 66a 扩：返回 (version, cred_hash, switched) 三元组——凭证摘要变化亦触发切换
（D26 §4.2 待实施项闭合：凭证轮换 → exit 9 重启重读行）。
"""
from unittest.mock import MagicMock, patch


def _r(version):
    r = MagicMock()
    r.get.return_value = version
    return r


def _iface(provider, credentials=None):
    return {"provider": provider, "credentials": credentials or {"app_id": "A", "app_secret": "S"},
            "params": {}, "market": "astock", "capabilities": ["trading"]}


def test_first_poll_baseline():
    """首次 poll（prev=None）建基线：版本+凭证摘要同轮建立（漏建=首次凭证轮换被漏），不判切换。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row", return_value=_iface("xtp")):
        v, h, switched = _poll_iface_switch(_r("5"), None, None, "xtp")
    assert v == "5" and h is not None and switched is False


def test_first_poll_baseline_read_fail_defers():
    """首发读行失败 → 版本顺延 None（不固化——固化会短路死锁基线，盲审 A-P1）下轮重走首发。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row", side_effect=Exception("db down")):
        v, h, switched = _poll_iface_switch(_r("5"), None, None, "xtp")
    assert v is None and h is None and switched is False


def test_baseline_rebuild_after_first_poll_failure_full_arc():
    """盲审 A-P1 全弧钉：首发失败（版本悬空）→ 下轮重建基线 → 再凭证轮换触发 True。

    防回归：任何「失败轮固化版本」的写法会让第 3 步 prev_hash=None 漏检。
    """
    from src.md_hub.main import _poll_iface_switch
    # ① 首发读行失败 → (None, None, False)
    with patch("src.strategy_framework.broker.get_interface_row", side_effect=Exception("db down")):
        v1, h1, s1 = _poll_iface_switch(_r("5"), None, None, "xtp")
    assert (v1, h1, s1) == (None, None, False)
    # ② 下轮（版本不变）重走首发 → 基线建立
    with patch("src.strategy_framework.broker.get_interface_row", return_value=_iface("xtp", {"app_id": "A", "app_secret": "OLD"})):
        v2, h2, s2 = _poll_iface_switch(_r("5"), v1, None, "xtp")
    assert v2 == "5" and h2 is not None and s2 is False
    # ③ 凭证轮换（版本 bump + 摘要变）→ 触发
    with patch("src.strategy_framework.broker.get_interface_row", return_value=_iface("xtp", {"app_id": "A", "app_secret": "NEW"})):
        v3, h3, s3 = _poll_iface_switch(_r("6"), v2, None, "xtp", h2)
    assert v3 == "6" and s3 is True


def test_no_version_change():
    """版本无变化 → 不重读接口行、不切换。"""
    from src.md_hub.main import _poll_iface_switch
    v, h, switched = _poll_iface_switch(_r("5"), "5", None, "xtp")
    assert v == "5" and switched is False


def test_version_change_provider_switched():
    """版本变化 + provider 变化 → 切换 True。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row", return_value=_iface("emt_emq")):
        v, h, switched = _poll_iface_switch(_r("6"), "5", None, "xtp")
    assert v == "6" and switched is True


def test_version_change_provider_same_cred_same():
    """版本变化但 provider+凭证均不变（改 params/position）→ 不切换；凭证基线本轮建立。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row", return_value=_iface("xtp")):
        v, h, switched = _poll_iface_switch(_r("6"), "5", None, "xtp")
    assert v == "6" and switched is False and h is not None


def test_version_change_cred_rotated_switches():
    """批 66a 钉：版本变化 + provider 不变但凭证轮换 → 切换 True（exit 9 重启重读）。"""
    from src.md_hub.main import _poll_iface_switch, _cred_hash
    prev_hash = _cred_hash({"app_id": "A", "app_secret": "OLD"})
    with patch("src.strategy_framework.broker.get_interface_row",
               return_value=_iface("xtp", {"app_id": "A", "app_secret": "NEW"})):
        v, h, switched = _poll_iface_switch(_r("6"), "5", None, "xtp", prev_hash)
    assert v == "6" and switched is True and h != prev_hash


def test_cred_baseline_none_no_false_trigger():
    """凭证基线未建立（首轮 prev_hash=None）→ 摘要变化不触发（与 provider 判定独立防误触）。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row",
               return_value=_iface("xtp", {"app_id": "A", "app_secret": "NEW"})):
        v, h, switched = _poll_iface_switch(_r("6"), "5", None, "xtp", None)
    assert switched is False and h is not None


def test_valkey_fail_keeps_prev():
    """Valkey 读失败 → 返回原版本、不切换（下轮再试）。"""
    from src.md_hub.main import _poll_iface_switch
    r = MagicMock()
    r.get.side_effect = Exception("down")
    v, h, switched = _poll_iface_switch(r, "5", None, "xtp")
    assert v == "5" and switched is False


def test_get_iface_fail_defers_version_recheck():
    """版本变化轮读行失败 → 版本顺延（固化新版本+旧摘要=漏掉本轮即轮换的检测，66a 双审修）。"""
    from src.md_hub.main import _poll_iface_switch
    with patch("src.strategy_framework.broker.get_interface_row", side_effect=Exception("db down")):
        v, h, switched = _poll_iface_switch(_r("6"), "5", None, "xtp")
    assert v == "5" and switched is False   # 版本保持旧值 → 下轮 v=6≠5 再读行


def test_cred_rotation_on_flaky_db_not_lost():
    """版本变化轮 DB 瞬断不吞轮换：读失败轮版本顺延 → 下轮重读 → 轮换检出。"""
    from src.md_hub.main import _poll_iface_switch, _cred_hash
    h_old = _cred_hash({"app_id": "A", "app_secret": "OLD"})
    # ① 轮换发生（v6）但读行瞬断 → 版本顺延
    with patch("src.strategy_framework.broker.get_interface_row", side_effect=Exception("db down")):
        v1, h1, s1 = _poll_iface_switch(_r("6"), "5", None, "xtp", h_old)
    assert (v1, s1) == ("5", False)
    # ② 下轮重读成功（新摘要）→ 触发
    with patch("src.strategy_framework.broker.get_interface_row",
               return_value=_iface("xtp", {"app_id": "A", "app_secret": "NEW"})):
        v2, h2, s2 = _poll_iface_switch(_r("6"), v1, None, "xtp", h_old)
    assert v2 == "6" and s2 is True
