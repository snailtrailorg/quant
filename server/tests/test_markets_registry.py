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
    assert provider_capabilities("tushare") == {"hist_quote", "ref_data"}   # MAP 30 项全集（0094+0106）归一；adapter 声明 20 串保持不变
    assert provider_capabilities("xtp") == {"trading", "rt_quote"}
    ok, msg = check_capability_subset("tushare", {"hist_quote", "trading"})
    assert not ok and "启用子集" in msg   # 越集拒
    assert check_capability_subset("tencent", {"rt_quote"})[0]


def test_sync_id_cap_map_covers_30():
    """批 64a：SYNC_ID_CAP_MAP 键数钉——与 sync_kind_config 行集（0094 二十 + 0106 十）防漂移。"""
    from src.quant_common.markets import SYNC_ID_CAP_MAP
    assert len(SYNC_ID_CAP_MAP) == 30
    pool_ten = {"income", "balancesheet", "cashflow", "fina_indicator", "cyq_chips",
                "top10_holders", "dividend", "pledge_stat", "share_float", "stk_holdernumber"}
    assert pool_ten <= set(SYNC_ID_CAP_MAP)          # 0106 增量行全在
    assert {SYNC_ID_CAP_MAP[s] for s in pool_ten} == {"ref_data"}  # 派生类一致（financial_stmt/featured_daily/holder_structure 均 ref_data）


def test_cap_label_mirror():
    """批 64a：能力标签三处镜像守门——markets.py（token+zh 标签）/ InterfacesCard CAP_TOKENS / locales cap_* 词条。

    N 语言架构兼容（加语言=只加条目零逻辑改动）：断言「全键等频 ≥2」而非「恰 2 次」——
    等频断裂=某语言漏条目，恰好是要抓的漂移；zh 块在前（缺省语言），首现值==后端标签。
    正则零命中即 fail（防静默）。
    """
    import re
    from pathlib import Path
    from src.quant_common.markets import CAPABILITIES, CAP_CLASS_LABELS

    web_root = Path(__file__).resolve().parents[2] / "web" / "src"

    # ① 前端 CAP_TOKENS 字面量 == 后端 CAPABILITIES
    vue = (web_root / "components" / "InterfacesCard.vue").read_text(encoding="utf-8")
    m = re.search(r"CAP_TOKENS\s*=\s*\[([^\]]*)\]", vue)
    assert m, "InterfacesCard.vue CAP_TOKENS 未命中（组件重构？）"
    assert set(re.findall(r"['\"](\w+)['\"]", m.group(1))) == set(CAPABILITIES)

    # ② locales cap_* 词条：全键等频 ≥2，zh 首现值==CAP_CLASS_LABELS
    js = (web_root / "locales" / "index.js").read_text(encoding="utf-8")
    counts, first_val = {}, {}
    for tok in CAPABILITIES:
        ms = re.findall(rf"cap_{tok}:\s*'([^']+)'", js)
        assert ms, f"locales 缺 interfaces.cap_{tok} 词条"
        counts[tok] = len(ms)
        first_val[tok] = ms[0]
    assert len(set(counts.values())) == 1 and next(iter(counts.values())) >= 2, \
        f"cap_* 词条出现次数不等频（漏某语言条目）: {counts}"
    assert first_val == dict(CAP_CLASS_LABELS), f"zh 词条与后端标签漂移: {first_val}"

    # ③ 反向镜像（代码审 B-P2-2 补）：locales 孤儿词条设防——后端删 token 而词条残留=漂移
    orphan = set(re.findall(r"cap_(\w+):\s*'", js)) - set(CAPABILITIES)
    assert not orphan, f"locales 残留已退役 token 词条: {orphan}"


def test_l0_single_source():
    from src.web_api.routes.trading import LIVE_TRADING_MARKETS
    from src.data_platform.perm_registry import MARKET_OP_KEYS
    assert LIVE_TRADING_MARKETS == MARKET_OP_KEYS   # L0:双源消(原 trading.py 硬编码副本)
