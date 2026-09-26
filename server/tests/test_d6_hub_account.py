"""D6 加密 per-account hub 部署测试：键 account 化 + SA4 期望态按 account + per-account 维护键。"""
from unittest.mock import MagicMock

from src.md_hub.parts import _key, LEASE_KEY


class TestKeyAccount:
    def test_none_returns_base(self):
        """A股 account_id=None：键不变（零回归）。"""
        assert _key(LEASE_KEY, None) == "hub:lease"

    def test_account_suffix(self):
        """加密 account_id=3：键加 :3 后缀（分 account 无串键）。"""
        assert _key(LEASE_KEY, 3) == "hub:lease:3"


class TestDesiredUnitsCryptoHub:
    def test_trading_account_adds_hub_unit(self):
        """批 66b（D26）：trading 域 account（enabled，全市场无 market 过滤）→ quant-md-hub@{id} 期望项。"""
        from src.scheduler import tasks as T
        import tests.test_sa4_reconciler as sa4
        from unittest.mock import patch
        conn = sa4._mk_conn2(crypto_ids=[3, 7])
        with patch.object(T, "_sa4_strategy_unit_files", return_value=[]):
            desired = T._desired_units(conn)
        assert ("quant-md-hub@3.service", "hub") in desired
        assert ("quant-md-hub@7.service", "hub") in desired
        assert not any(s == "builtin" for _, s in desired)   # builtin 常开条目已退役（66b）

    def test_no_trading_account_no_hub(self):
        from src.scheduler import tasks as T
        import tests.test_sa4_reconciler as sa4
        from unittest.mock import patch
        conn = sa4._mk_conn2()   # crypto_ids 空
        with patch.object(T, "_sa4_strategy_unit_files", return_value=[]):
            desired = T._desired_units(conn)
        assert not any(s == "hub" for _, s in desired)

    def test_no_market_filter_in_sql(self):
        """批 66b 钉：期望源 SQL 去 market='crypto' 过滤（全市场行驱动）。"""
        from src.scheduler import tasks as T
        import tests.test_sa4_reconciler as sa4
        from unittest.mock import patch
        conn = sa4._mk_conn2(crypto_ids=[3])
        with patch.object(T, "_sa4_strategy_unit_files", return_value=[]):
            T._desired_units(conn)
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        hub_sql = [s for s in sqls if "external_interface" in s]
        assert hub_sql and not any("market='crypto'" in s for s in hub_sql)


class TestSa4HubGuardsAccount:
    def test_per_account_maint_key(self):
        """D6：account A 维护键不拦 account B 的自愈拉起。"""
        from src.scheduler import tasks as T
        r = MagicMock()
        r.exists.side_effect = lambda key: key == "quant:maintenance:md-hub:3"
        ok_a, reason_a = T._sa4_hub_guards(r, 3)
        ok_b, reason_b = T._sa4_hub_guards(r, 7)
        assert (ok_a, reason_a) == (False, "maintenance")
        assert ok_b is True   # account 7 无维护键，可拉起

    def test_astock_global_key_unchanged(self):
        """A股 account_id=None：全局键（现状）。"""
        from src.scheduler import tasks as T
        r = MagicMock()
        r.exists.return_value = 0
        ok, reason = T._sa4_hub_guards(r, None)
        assert ok is True
        assert r.exists.call_args_list and r.exists.call_args_list[0][0][0] == "hub:lease"
