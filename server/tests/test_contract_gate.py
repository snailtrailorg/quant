"""批 56a·M0 契约层测试钉+CI 三断言（29 号 §三验收判据①③）。

三断言执法机制（29 号 v3 六审立法）：
1. 无 if provider==——静态正则扫 src/**；白名单=tests/fixtures/provider_gate_whitelist.txt（现状基线）
2. adapter 输出列白名单——本批钉常量（ALLOWED_BAR_COLUMNS/SNAPSHOT），to_contract 运行时断言 M3 接入
3. CapabilityDecl 组合校验——register_adapter 钩子（import 即执法）；本文件直测校验函数
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.quant_common.contract import (
    DATA_KINDS, KIND_TEMPORALITY, TEMPORALITIES, AsOf, ASTOCK_ALL,
    ASTOCK_SHSE_SZSE, ALLOWED_BAR_COLUMNS, ALLOWED_SNAPSHOT_COLUMNS,
    BAR_COLUMNS, CapabilityDecl, ContractError, ContractEvent, DataGap,
    DataRequest, ParamError, Quality, Scope, SourceUnavailable, SnapshotRow,
    Subscription, CRYPTO_ALL, is_legal, validate_capability_decls,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))       # server/
SRC = os.path.join(REPO, "src")
assert os.path.isdir(SRC), f"扫描目录不存在——CI 断言将空转（曾经指到仓库根拼接而恒空，盲审 B）: {SRC}"


class TestKindRegistry:
    """品类词表与组合表（28 §十一的逐格钉）。"""

    def test_26_kinds(self):
        assert len(DATA_KINDS) == 26, sorted(DATA_KINDS)  # 行情7+参考10+事件2+交易4+占位3

    def test_every_kind_has_combo_row(self):
        assert set(KIND_TEMPORALITY) == set(DATA_KINDS)   # 无孤儿 kind/无幽灵行

    def test_combo_table_full_grid(self):
        for kind, ts in KIND_TEMPORALITY.items():
            assert ts <= TEMPORALITIES and ts, f"{kind} 空或非法时态集 {ts}"

    def test_is_legal_pos_neg(self):
        assert is_legal("bar_daily", "historical")
        assert is_legal("bar_minute", "streaming")         # hybrid
        assert is_legal("funding_rate", "snapshot")        # 当期预测
        assert not is_legal("bar_daily", "streaming")
        assert not is_legal("snapshot", "historical")
        assert not is_legal("no_such_kind", "historical")  # 未知恒 False


class TestScope:
    def test_covers_none_wildcard(self):
        s = ASTOCK_ALL
        assert s.covers_one("astock", "BSE", "reits")     # 全所全品类通配
        assert not s.covers_one("crypto", "BINANCE", "perp")

    def test_covers_exchange_filter(self):
        s = ASTOCK_SHSE_SZSE
        assert s.covers_one("astock", "SHSE", "stock")
        assert not s.covers_one("astock", "BSE", "stock")  # stk_mins 无 BSE 覆盖事实

    def test_covers_category_filter(self):
        s = Scope(frozenset({"astock"}), None, frozenset({"convertible"}))
        assert s.covers_one("astock", "SHSE", "convertible")
        assert not s.covers_one("astock", "SHSE", "stock")

    def test_markets_nonempty_enforced(self):
        # 29 §三立法：markets 非空——全市场=显式列全集，防跨市场通配非法态（盲审 A/B 收回钉）
        with pytest.raises(ValueError):
            Scope(frozenset(), None, None)
        with pytest.raises(ValueError):
            Scope(None, None, None)  # type: ignore[arg-type]


class TestDataRequest:
    def test_defaults(self):
        r = DataRequest(kind="bar_daily", symbols=("600000.SHSE",), temporality="historical")
        assert r.sub_kind is None and r.mode == "consume" and r.as_of is AsOf.LATEST
        assert r.deadline_ms == 10_000 and r.consumer_tag == "default"

    def test_frozen(self):
        r = DataRequest(kind="bar_daily", symbols=(), temporality="historical")
        with pytest.raises(Exception):
            r.kind = "snapshot"  # type: ignore[misc]


class TestColumns:
    def test_bar_11_fields(self):
        assert len(BAR_COLUMNS) == 11
        assert "source" in ALLOWED_BAR_COLUMNS            # 血缘第一环
        assert "adj_factor" in ALLOWED_BAR_COLUMNS

    def test_no_adjusted_price_columns(self):
        for bad in ("close_qfq", "open_hfq", "adjusted_close"):
            assert bad not in ALLOWED_BAR_COLUMNS          # 复权立法（28 §3.4）

    def test_snapshot_row_fields(self):
        for k in ("mark_price", "index_price", "funding_rate_next", "limit_up", "limit_down"):
            assert k in ALLOWED_SNAPSHOT_COLUMNS


class TestEvents:
    def test_contract_event_shape(self):
        e = ContractEvent(kind="stream_bar", gen=3, seq=17, payload={"close": 1.0},
                          ts_receive=datetime.now(timezone.utc))
        assert (e.gen, e.seq) == (3, 17)

    def test_subscription_watermark_default(self):
        s = Subscription(kind="stream_bar", symbols=())
        assert s.from_watermark is None


class TestErrorTaxonomy:
    def test_hierarchy(self):
        for cls in (ParamError, SourceUnavailable, DataGap):
            assert issubclass(cls, ContractError)


class TestCapabilityDeclValidation:
    def test_valid(self):
        decls = [
            CapabilityDecl("bar_daily", "historical", ASTOCK_ALL),
            CapabilityDecl("featured_daily", "historical", ASTOCK_ALL,
                           sub_kinds=frozenset({"moneyflow", "top_list"})),
        ]
        assert validate_capability_decls(decls) == []

    def test_illegal_combo(self):
        errs = validate_capability_decls([CapabilityDecl("snapshot", "historical", ASTOCK_ALL)])
        assert any("illegal combo" in e for e in errs)

    def test_unknown_kind(self):
        errs = validate_capability_decls([CapabilityDecl("ghost", "historical", ASTOCK_ALL)])
        assert any("unknown kind" in e for e in errs)

    def test_subkinds_mismatch(self):
        # 聚合域没声明 sub_kinds / 非聚合域带了 sub_kinds —— 双向都抓
        e1 = validate_capability_decls([CapabilityDecl("featured_daily", "historical", ASTOCK_ALL)])
        e2 = validate_capability_decls([CapabilityDecl("bar_daily", "historical", ASTOCK_ALL,
                                                       sub_kinds=frozenset({"x"}))])
        assert any("sub_kinds mismatch" in e for e in e1)
        assert any("sub_kinds mismatch" in e for e in e2)


# ─────────────── CI 断言一：全仓无 if provider ==（白名单文件豁免） ───────────────

WHITELIST_FILE = os.path.join(os.path.dirname(__file__), "fixtures", "provider_gate_whitelist.txt")
# 正则：if <expr> == "provider字面量" 或 == 'x'（含 != 形态与 provider 在两侧）
PROVIDER_CMP = re.compile(
    r"""(?:if|elif|while)\s+[^:\n]*?\bprovider\b\s*(?:==|!=)\s*['"][a-z_]+['"]"""
    r"""|(?:if|elif|while)\s+['"][a-z_]+['"]\s*(?:==|!=)\s*[^:\n]*?\bprovider\b"""
)


def _load_whitelist() -> set[str]:
    """白名单条目=相对路径:行号（29 号 §三"一行一符号"——模块级豁免会静音同文件真违规，盲审 B）。"""
    if not os.path.exists(WHITELIST_FILE):
        return set()
    out = set()
    for line in open(WHITELIST_FILE):
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


class TestProviderGate:
    def test_no_hardcoded_provider_branches(self):
        whitelist = _load_whitelist()
        offenders = []
        for root, dirs, files in os.walk(SRC):
            dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", ".git")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                rel = os.path.relpath(path, SRC)
                for i, line in enumerate(open(path, encoding="utf-8"), 1):
                    if PROVIDER_CMP.search(line) and f"{rel}:{i}" not in whitelist:
                        offenders.append(f"{rel}:{i}: {line.strip()[:80]}")
        assert not offenders, (
            "发现硬编码 provider 分支（M3 起收敛至零；如属 adapter 方言处理请在白名单加 "
            f"'相对路径:行号' 一行一条——{WHITELIST_FILE}）:\n" + "\n".join(offenders[:20])
        )

    @pytest.mark.skipif(not os.path.exists(WHITELIST_FILE), reason="基线未生成")
    def test_whitelist_entries_exist(self):
        """白名单条目必须指向真实文件与行内容（防漂移死条目）。"""
        for key in _load_whitelist():
            rel, _, lineno = key.partition(":")
            path = os.path.join(SRC, rel)
            assert os.path.exists(path), f"白名单死条目（文件不存在）: {key}"
            if lineno.isdigit():
                lines = open(path, encoding="utf-8").readlines()
                assert int(lineno) <= len(lines), f"白名单死条目（行号越界）: {key}"


# ─────────────── register_adapter 钩子（CI 断言三接线验证） ───────────────

class TestRegisterHook:
    def test_hook_wires_capability_decls(self):
        """register_adapter 装饰器对声明了 capability_decls 的类做即时校验（import 即执法）。"""
        from src.data_platform.adapters.base import register_adapter, _ADAPTERS
        try:
            @register_adapter
            class _Bad:
                provider = "__bad_decl_test__"
                capability_decls = [CapabilityDecl("snapshot", "historical", ASTOCK_ALL)]
            raise AssertionError("非法声明未被装饰器拦截")
        except ValueError as e:
            assert "capability" in str(e).lower() or "illegal" in str(e).lower()
        finally:
            _ADAPTERS.pop("__bad_decl_test__", None)
