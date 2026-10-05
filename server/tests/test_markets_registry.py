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


def test_sync_id_cap_map_completeness():
    """批 64a 立、批 99 改造：由「字面量 30 键」改为「结构化完备性」——字面量键数每加一项同步
    就要改一次数字（且改数字比改内容省事 ⇒ 容易被顺手放宽），故改为钉**结构性事实**：
    ① 0106 池内十表全在且一致归 ref_data；② 批 99 补的四缺项在；③ 全部取值 ∈ CAPABILITIES。
    （真库侧的「⊇ sync_config.id ∪ sync_kind_config.sync_id」完备性钉见
    `tests/test_batch99_sync_backfill_capability.py::TestContractWiring`。）
    """
    from src.quant_common.markets import CAPABILITIES, SYNC_ID_CAP_MAP
    pool_ten = {"income", "balancesheet", "cashflow", "fina_indicator", "cyq_chips",
                "top10_holders", "dividend", "pledge_stat", "share_float", "stk_holdernumber"}
    assert pool_ten <= set(SYNC_ID_CAP_MAP)          # 0106 增量行全在
    assert {SYNC_ID_CAP_MAP[s] for s in pool_ten} == {"ref_data"}  # 派生类一致（financial_stmt/featured_daily/holder_structure 均 ref_data）
    # 批 99：批 83b 收编 0118/0119 时漏登记的 4 项（有 sync_config 行却在映射表缺席）
    assert {"convertible_terms", "static_symbols",
            "pool_data", "pool_data_full_calibrate"} <= set(SYNC_ID_CAP_MAP)
    assert set(SYNC_ID_CAP_MAP.values()) <= set(CAPABILITIES)


def test_cap_label_mirror():
    """能力词表守门：后端 token 单一真源 + 前端零字面量消费 + locales 标签全覆盖。

    批 83a 重构：原「三处镜像」（markets.py / InterfacesCard CAP_TOKENS / locales cap_*）
    退化为「一处真源 + 一层派生」——前端曾镜像 5 token 字面量，拆表后由 `/providers`
    响应回 `domain_capabilities`（同源 `markets.DOMAIN_CAPS`），镜像点消除（镜像=漂移源）。
    故 ① 由「字面量 == CAPABILITIES」改为「无字面量 + 真消费 registry 字段」——
    更严的等价物：断言零字面量防止镜像回潮，再断言消费链不断（配合 test_config_plane
    的 `/providers` 响应断言，构成 后端词表 → HTTP → 前端 的完整链路钉）。

    N 语言架构兼容（加语言=只加条目零逻辑改动）：断言「全键等频 ≥2」而非「恰 2 次」——
    等频断裂=某语言漏条目，恰好是要抓的漂移；zh 块在前（缺省语言），首现值==后端标签。
    正则零命中即 fail（防静默）。
    """
    import re
    from pathlib import Path
    from src.quant_common.markets import CAPABILITIES, CAP_CLASS_LABELS

    web_root = Path(__file__).resolve().parents[2] / "web" / "src"

    # ① 前端零字面量（83a）：能力 token 表不再前端镜像
    vue = (web_root / "components" / "InterfacesCard.vue").read_text(encoding="utf-8")
    assert not re.search(r"CAP_TOKENS\s*=\s*\[", vue), \
        "InterfacesCard.vue 重现能力 token 字面量镜像（83a 已改注册表驱动，镜像=漂移源）"
    assert "domain_capabilities" in vue, \
        "InterfacesCard.vue 未消费后端 domain_capabilities 字段（能力集来源断链）"

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
