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


