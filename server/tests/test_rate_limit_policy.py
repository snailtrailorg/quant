"""限速策略测试（24 号限速抽象聚合）。"""
from src.data_platform.rate_limit import FixedIntervalPolicy, DailyQuotaPolicy
from src.data_platform.data_source import TushareDataSource


def test_fixed_interval_two_levels():
    """FixedIntervalPolicy：类默认 + DB 覆写两级。"""
    p = FixedIntervalPolicy({"daily": 0.5, "stk_mins": 3600.0}, {"stk_mins": 60})
    assert p.get_interval("daily") == 0.5
    assert p.get_interval("stk_mins") == 60.0
    assert p.get_interval("ghost") == 0.0


def test_fixed_interval_invalid_override_falls_back():
    """非法覆写回落类默认（同原 get_rate_limit 容错）。"""
    p = FixedIntervalPolicy({"daily": 0.5}, {"daily": "bad"})
    assert p.get_interval("daily") == 0.5


def test_daily_quota_average_interval():
    """DailyQuotaPolicy：每日额度 → 平均间隔 86400/N。"""
    p = DailyQuotaPolicy(1000)
    assert abs(p.get_interval("any") - 86.4) < 1e-9


def test_datasource_delegates_to_policy():
    """DataSource.get_rate_limit 委托限速策略（两级）。"""
    ds = TushareDataSource(params='{"rate_limits": {"stk_mins": 60}}')
    assert ds.get_rate_limit("stk_mins") == 60.0
    assert ds.get_rate_limit("daily") == 0.5


def test_adapter_capabilities():
    """adapter capabilities 类属性（供给矩阵数据源）。"""
    from src.data_platform.adapters.base import TushareAdapter
    assert "astock_daily" in TushareAdapter.capabilities
    assert "trade_cal" not in TushareAdapter.capabilities   # 静态/日历非 adapter 路由
