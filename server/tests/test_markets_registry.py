"""批55-0:维度注册表+能力词表映射+⊆校验 钉。D25 v2：能力集 token 立法+派生全覆盖互斥钉。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_registry_shapes():
    from src.quant_common.markets import (MARKETS, EXCHANGES, CAPABILITIES, MARKET_OP_DECOMP,
                                          KIND_CAP_CLASS, CAP_CLASS_LABELS)
    assert set(MARKETS) == {"astock", "crypto"}
    assert all(v["market"] in MARKETS for v in EXCHANGES.values())
    assert set(MARKET_OP_DECOMP) == {"convertible", "etf", "astock", "binance_perp", "okx_perp"}
    d = MARKET_OP_DECOMP["binance_perp"]
    assert d == ("crypto", "perp", "BINANCE")   # 三分混一立法化解
    # D25 v2：能力集 5 token（trading 保词——8 处 SQL 域谓词零改动）
    assert set(CAPABILITIES) == {"hist_quote", "rt_quote", "trading", "ref_data", "inst_event"}
    assert set(CAP_CLASS_LABELS) == set(CAPABILITIES)
    # D25 v2：派生映射全覆盖互斥（31 项漏网的根因=无此断言；加 DataKind 必加 KIND_CAP_CLASS 行）
    from src.quant_common.contract import DATA_KINDS
    assert set(KIND_CAP_CLASS) == set(DATA_KINDS)             # 每个 DataKind 恰归一类
    assert set(KIND_CAP_CLASS.values()) == set(CAPABILITIES)  # 归类值域=能力集且每类非空


def test_capability_map_and_subset():
    from src.data_platform.capabilities import provider_capabilities, check_capability_subset
    assert provider_capabilities("tushare") == {"hist_quote", "ref_data"}   # 20 sync_id 归一（0094 全集）
    assert provider_capabilities("xtp") == {"trading", "rt_quote"}
    ok, msg = check_capability_subset("tushare", {"hist_quote", "trading"})
    assert not ok and "启用子集" in msg   # 越集拒
    assert check_capability_subset("tencent", {"rt_quote"})[0]


def test_l0_single_source():
    from src.web_api.routes.trading import LIVE_TRADING_MARKETS
    from src.data_platform.perm_registry import MARKET_OP_KEYS
    assert LIVE_TRADING_MARKETS == MARKET_OP_KEYS   # L0:双源消(原 trading.py 硬编码副本)
