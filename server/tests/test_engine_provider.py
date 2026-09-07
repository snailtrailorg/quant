"""engine 数据源路由测试（24 号多数据源架构，盲审 B-P2 补）。"""
from unittest.mock import patch

from src.data_platform.adapters.base import TushareAdapter, JoinQuantAdapter


def test_get_kline_adapter_routes_by_provider():
    """_get_kline_adapter 按 provider 路由。"""
    from src.data_sync import engine
    assert isinstance(engine._get_kline_adapter({"provider": "tushare"}), TushareAdapter)
    assert isinstance(engine._get_kline_adapter({"provider": "joinquant"}), JoinQuantAdapter)


def test_get_kline_adapter_fallback_unknown():
    """未知 provider 回退 tushare（配置错不打断同步）。"""
    from src.data_sync import engine
    assert isinstance(engine._get_kline_adapter({"provider": "unknown_provider"}), TushareAdapter)


def test_get_pro_api_returns_adapter():
    """_get_pro_api 返回 (adapter, kind, freq, bar_type)。"""
    from src.data_sync import engine
    with patch("src.data_sync.engine._get_config", return_value={"provider": "tushare"}):
        adapter, kind, freq, bar_type = engine._get_pro_api("astock_daily")
    assert isinstance(adapter, TushareAdapter)
    assert kind == "astock"
    assert freq == "1D"
    assert bar_type == "daily"
