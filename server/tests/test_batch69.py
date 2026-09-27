"""批 69：SSE publish 两挂点+L2 探测三态+心跳字段导出钉。"""
from unittest.mock import MagicMock, patch


class TestSsePublish:
    def test_on_trade_publishes_frame(self):
        """on_trade：write_trade_log 后发 trade_update 零内容帧（bus 方法形态）。"""
        import src.strategy_runner.hub_worker as hw
        fake_bus = MagicMock()
        with patch("src.strategy_runner.trading.write_trade_log") as wt, \
             patch("src.quant_common.eventbus.bus", fake_bus):
            handler = None
            # 直接验证 import 形态可解析（bus.publish_cross_process 方法存在性+调用形态）
            from src.quant_common.eventbus import bus
            assert hasattr(bus, "publish_cross_process")
        # 源码钉：worker on_trade 体内含 bus.publish_cross_process(0, "trade_update"
        import inspect
        src = inspect.getsource(hw)
        assert 'bus.publish_cross_process(0, "trade_update", {})' in src

    def test_on_order_publishes_frame(self):
        import src.strategy_runner.main as m
        import inspect
        src = inspect.getsource(m)
        assert 'bus.publish_cross_process(0, "order_update", {})' in src


class TestL2Probe:
    def _r(self, hub_hash=None, task_hashes=None):
        r = MagicMock()
        def hgetall(k):
            if k.startswith("quant:hb:md-hub:"):
                return hub_hash if hub_hash is not None else {}
            return (task_hashes or {}).get(k, {})
        r.hgetall.side_effect = hgetall
        return r

    def _conn(self, tids=()):
        conn = MagicMock(); conn.__enter__.return_value = conn
        cur = MagicMock(); cur.fetchall.return_value = [(t,) for t in tids]
        conn.execute.return_value = cur
        return conn

    def test_hub_missing_key_distinct(self):
        """键不存在（SA4 延迟）hub_key_exists=False 区分字段缺失（部署间隙）。"""
        from src.data_platform.trade_switch import _l2_probe
        with patch("redis.Redis.from_url", return_value=self._r(hub_hash=None)), \
             patch("src.data_platform.db.get_conn", return_value=self._conn()), \
             patch("src.quant_common.session.in_session", return_value=True):
            d = _l2_probe(4)
        assert d["hub_key_exists"] is False and d["hub_connected"] is None

    def test_field_missing_deploy_gap(self):
        from src.data_platform.trade_switch import _l2_probe
        with patch("redis.Redis.from_url", return_value=self._r(hub_hash={"gen": "5"})), \
             patch("src.data_platform.db.get_conn", return_value=self._conn()), \
             patch("src.quant_common.session.in_session", return_value=True):
            d = _l2_probe(4)
        assert d["hub_key_exists"] is True and d["hub_connected"] is None

    def test_connected_with_tasks_all_agg(self):
        from src.data_platform.trade_switch import _l2_probe
        tasks = {"quant:hb:task:8": {"td": "1"}, "quant:hb:task:9": {"td": "1"}}
        with patch("redis.Redis.from_url", return_value=self._r({"connected": "1"}, tasks)), \
             patch("src.data_platform.db.get_conn", return_value=self._conn(tids=[8, 9])), \
             patch("src.quant_common.session.in_session", return_value=True):
            d = _l2_probe(4)
        assert d["hub_connected"] is True and d["td_connected"] is True and d["running_tasks"] == 2

    def test_valkey_down_fail_open(self):
        from src.data_platform.trade_switch import _l2_probe
        with patch("redis.Redis.from_url", side_effect=RuntimeError("down")):
            d = _l2_probe(4)
        assert d["hub_key_exists"] is None   # fail-open 展示（不阻塞切换）

    def test_get_session_live_recalc_for_nominated(self):
        """P1-2：nominated/confirmed 态详情实时重算 l2（不落库）。"""
        import inspect
        from src.data_platform import trade_switch as ts
        src = inspect.getsource(ts.get_session)
        assert "_l2_probe" in src
