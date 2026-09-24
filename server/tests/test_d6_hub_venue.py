"""D6 加密 per-venue hub 部署测试：键 venue 化 + SA4 期望态按 venue + per-venue 维护键。"""
from unittest.mock import MagicMock

from src.md_hub.parts import _key, LEASE_KEY


class TestKeyVenue:
    def test_none_returns_base(self):
        """A股 venue_id=None：键不变（零回归）。"""
        assert _key(LEASE_KEY, None) == "hub:lease"

    def test_venue_suffix(self):
        """加密 venue_id=3：键加 :3 后缀（分 venue 无串键）。"""
        assert _key(LEASE_KEY, 3) == "hub:lease:3"


class TestDesiredUnitsCryptoHub:
    def test_crypto_venue_adds_hub_unit(self):
        """D6：加密 venue（enabled + trading + crypto）→ quant-md-hub@{venue_id} 期望项。"""
        from src.scheduler import tasks as T
        import tests.test_sa4_reconciler as sa4
        from unittest.mock import patch
        conn = sa4._mk_conn2(crypto_ids=[3, 7])
        with patch.object(T, "_sa4_strategy_unit_files", return_value=[]):
            desired = T._desired_units(conn)
        assert ("quant-md-hub@3.service", "hub:crypto") in desired
        assert ("quant-md-hub@7.service", "hub:crypto") in desired
        assert (T.SA4_HUB_UNIT, "builtin") in desired   # A股单 hub 仍常开

    def test_no_crypto_venue_no_crypto_hub(self):
        from src.scheduler import tasks as T
        import tests.test_sa4_reconciler as sa4
        from unittest.mock import patch
        conn = sa4._mk_conn2()   # crypto_ids 空
        with patch.object(T, "_sa4_strategy_unit_files", return_value=[]):
            desired = T._desired_units(conn)
        assert not any(s == "hub:crypto" for _, s in desired)


class TestSa4HubGuardsVenue:
    def test_per_venue_maint_key(self):
        """D6：venue A 维护键不拦 venue B 的自愈拉起。"""
        from src.scheduler import tasks as T
        r = MagicMock()
        r.exists.side_effect = lambda key: key == "quant:maintenance:md-hub:3"
        ok_a, reason_a = T._sa4_hub_guards(r, 3)
        ok_b, reason_b = T._sa4_hub_guards(r, 7)
        assert (ok_a, reason_a) == (False, "maintenance")
        assert ok_b is True   # venue 7 无维护键，可拉起

    def test_astock_global_key_unchanged(self):
        """A股 venue_id=None：全局键（现状）。"""
        from src.scheduler import tasks as T
        r = MagicMock()
        r.exists.return_value = 0
        ok, reason = T._sa4_hub_guards(r, None)
        assert ok is True
        assert r.exists.call_args_list and r.exists.call_args_list[0][0][0] == "hub:lease"
