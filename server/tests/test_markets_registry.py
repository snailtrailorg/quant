"""批55-0:维度注册表+能力词表映射+⊆校验 钉。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_registry_shapes():
    from src.quant_common.markets import MARKETS, EXCHANGES, CAPABILITIES, MARKET_OP_DECOMP
    assert set(MARKETS) == {"astock", "crypto"}
    assert all(v["market"] in MARKETS for v in EXCHANGES.values())
    assert set(MARKET_OP_DECOMP) == {"convertible", "etf", "astock", "binance_perp", "okx_perp"}
    d = MARKET_OP_DECOMP["binance_perp"]
    assert d == ("crypto", "perp", "BINANCE")   # 三分混一立法化解


def test_capability_map_and_subset():
    from src.data_platform.capabilities import provider_capabilities, check_capability_subset
    assert provider_capabilities("tushare") == {"daily", "minute"}   # sync_id 串归一(astock_daily→daily)
    assert provider_capabilities("xtp") == {"trading", "quote"}
    ok, msg = check_capability_subset("tushare", {"daily", "trading"})
    assert not ok and "启用子集" in msg   # 越集拒
    assert check_capability_subset("tencent", {"minute", "snapshot"})[0]


def test_l0_single_source():
    from src.web_api.routes.trading import LIVE_TRADING_MARKETS
    from src.data_platform.perm_registry import MARKET_OP_KEYS
    assert LIVE_TRADING_MARKETS == MARKET_OP_KEYS   # L0:双源消(原 trading.py 硬编码副本)
