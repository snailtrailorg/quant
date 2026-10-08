"""破坏性迁移两步走立法闸门（批 111 产出 5①-④⑦——仓内立法执行点）。

⚠ 诚实边界（W6 第 1 条）：本闸门**不在部署路径上**（部署链零 pytest）——它是开发期
步 5 单测的纪律闸门；prod 侧机械强制点＝`release.yml` 阶段 4（判定真源同一模块）。

钉什么：
① 非 legacy 白名单文件命中回滚安全类 op ⇒ 唯一合规声明＝两步走的 phase=contract
  （pair 已上产且声明 expand）；phase=expand 携带任何命中 ⇒ 红（expand 只有「无命中」
  一种合法形态）。白名单 legacy 文件归 ④ 管（消除口径冲突）。
② phase=contract 的 pair 必须真实存在且声明 expand（本版目录即全链）。
③ 孤儿 expand（声明了 pair 而对手不在）⇒ 进报告**不红**（避免半成品迁移被误卡）；
  contract 无 expand 对手 ⇒ 红（归 ②）。
④ legacy 白名单双向校验（照 test_jsonb_columns_guarded.py 豁免表先例）：白名单**真源＝
  模块 `LEGACY_FROZEN`**（本文件只做别名，步 4 同判 P0-1）与文件内 legacy 标记互为子集；
  标记陈旧（已无命中）⇒ 红。
⑤ 真源非空（防空集假绿）。
⑥ 判定逐条取自产出 4 模块——本文件**不自建任何判定正则**（结构断言守）。
⑦ 模块内清单反向校验在 test_migration_policy.py（同族守卫），此处不重复。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.data_platform.migration_policy import (
    LEGACY_FROZEN,
    evaluate,
    parse_declaration,
    scan_unsafe_lines,
)

_VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"

# 冻结的 legacy 白名单（批 111 处置结论表）。**真源在 `migration_policy.LEGACY_FROZEN`**
# （步 4 同判 P0-1：清单必须住执行侧模块，否则「谁知道合法」与「谁在 prod 执行」是两处）
# ——本处只作别名，**不得**再写第二份字面量。
# 双向校验：白名单外新建 legacy ⇒ 红；白名单内文件删 legacy 标记 ⇒ 红。
_LEGACY_EXPECTED: frozenset[str] = frozenset(LEGACY_FROZEN)

_TWO_STEP = {"0116": "0122", "0122": "0116"}  # 本仓唯一真正两步走的实例（批 85·a/c）


def _all_migrations() -> list[Path]:
    files = sorted(_VERSIONS.glob("*.py"))
    assert files, "真源非空（防空集假绿）"  # ⑤
    return files


def _decl_of(p: Path) -> dict:
    return parse_declaration(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# ① 非白名单命中 ⇒ 唯一合规＝contract；expand 不得携带命中
# ---------------------------------------------------------------------------

def test_nonlegacy_hit_must_be_contract_with_deployed_pair() -> None:
    for p in _all_migrations():
        decl = _decl_of(p)
        if decl["legacy"]:
            continue  # 归 ④
        if not scan_unsafe_lines(p.read_text(encoding="utf-8")):
            continue  # 无命中 ⇒ 无须声明
        ok, why = evaluate(p, _VERSIONS)
        assert ok, f"{p.name}: {why}（非白名单命中 ⇒ 唯一合规＝phase=contract）"


def test_expand_must_not_carry_hit() -> None:
    for p in _all_migrations():
        decl = _decl_of(p)
        if decl["phase"] != "expand":
            continue
        hits = scan_unsafe_lines(p.read_text(encoding="utf-8"))
        assert not hits, (
            f"{p.name}: phase=expand 携带回滚安全类 op（{hits[0][1]}）——"
            f"expand 只有「无命中」一种合法形态（P0-2 收紧）"
        )


# ---------------------------------------------------------------------------
# ②/③ pair 存在性与对手声明
# ---------------------------------------------------------------------------

def test_contract_pair_exists_and_declares_expand() -> None:
    for p in _all_migrations():
        decl = _decl_of(p)
        if decl["phase"] != "contract":
            continue
        pair = decl["pair"]
        assert pair, f"{p.name}: phase=contract 须带 pair"
        pair_files = sorted(_VERSIONS.glob(f"{pair}_*.py"))
        assert pair_files, f"{p.name}: pair={pair} 指向的 expand 不在本版迁移目录（本版目录即全链）"
        pair_decl = _decl_of(pair_files[0])
        assert pair_decl["phase"] == "expand", (
            f"{p.name}: pair={pair} 存在但未声明 phase=expand（对手={pair_files[0].name}）"
        )


def test_orphan_expand_reported_not_red() -> None:
    """expand 声明了 pair 而对手不在 ⇒ **允许**（pair 允许指向后续 contract），只报告、不判红。

    ⚠ 步 4 同判 P1-b：旧版误写 `assert not orphans`——把闸门③ / 规范 §4 的「孤儿允许」
    写成「禁止存在」，会**误卡正当进行中的两步走**。已改：孤儿是合法中间态。
    保留一条**方向**校验：pair 须指向**更晚**的 id（contract 在 expand 之后），防误标。
    """
    orphans = []
    for p in _all_migrations():
        decl = _decl_of(p)
        if decl["phase"] != "expand" or not decl["pair"]:
            continue
        assert decl["pair"] > p.name[:4], (
            f"{p.name}: pair={decl['pair']} 须晚于 expand 自身 id（两步走方向 expand→contract）"
        )
        if not sorted(_VERSIONS.glob(f"{decl['pair']}_*.py")):
            orphans.append(f"{p.name}: pair={decl['pair']}（contract 尚未产出——两步走进行中）")
    if orphans:
        print("ℹ 孤儿 expand（允许，两步走进行中，不判红）:")
        for ln in orphans:
            print("  ", ln)


# ---------------------------------------------------------------------------
# ④ legacy 白名单双向校验 + 陈旧检测
# ---------------------------------------------------------------------------

def test_legacy_whitelist_bidirectional() -> None:
    marked: set[str] = set()
    for p in _all_migrations():
        text = p.read_text(encoding="utf-8")
        decl = parse_declaration(text)
        if decl["legacy"]:
            marked.add(p.name)
        if decl["legacy"] and p.name not in _LEGACY_EXPECTED:
            pytest.fail(
                f"{p.name}: 白名单外新建 legacy——legacy 只能减不能增（加项须用户裁定 + 更新 _LEGACY_EXPECTED）"
            )
    extra = _LEGACY_EXPECTED - marked
    assert not extra, f"白名单内文件已无 legacy 标记（陈旧白名单，须移除）: {sorted(extra)}"


def test_legacy_entries_still_hit() -> None:
    """标记陈旧检测：白名单文件若已无命中，标记即谎报（保留只会让人误读历史）。"""
    for name in sorted(_LEGACY_EXPECTED):
        p = _VERSIONS / name
        assert p.exists(), f"白名单文件不存在: {name}"
        assert scan_unsafe_lines(p.read_text(encoding="utf-8")), (
            f"{name}: 已无回滚安全类命中但仍标 legacy（陈旧标记，须移除）"
        )


def test_two_step_pair_intact() -> None:
    """唯一两步走实例的收口断言：0116(expand)↔0122(contract) 声明互指且各自合规。"""
    for pid, pair in _TWO_STEP.items():
        files = sorted(_VERSIONS.glob(f"{pid}_*.py"))
        assert files, f"两步走实例缺失: {pid}"
        decl = _decl_of(files[0])
        assert decl["pair"] == pair, f"{files[0].name}: pair 应为 {pair}"
    assert _decl_of(_VERSIONS / "0116_split_external_interface.py")["phase"] == "expand"
    assert _decl_of(_VERSIONS / "0122_contract_drop_external_interface.py")["phase"] == "contract"


# ---------------------------------------------------------------------------
# ⑥ 本闸门不自建判定正则（判定逐条取自产出 4 模块）
# ---------------------------------------------------------------------------

def test_this_gate_builds_no_regex() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    # 字符串拼接规避自指（本断言的消息文本不得含被禁字面本身）。
    banned = "im" + "port re"
    assert banned not in src, "本闸门不得直接用正则模块——判定必须取自 migration_policy 模块"
    # 以下字面同理拼接，规避自指。
    assert ("re." + "compile") not in src and ("re." + "search") not in src


def test_module_not_empty_and_patterns_frozen() -> None:
    import src.data_platform.migration_policy as mp

    assert mp.ROLLBACK_UNSAFE_PATTERNS, "判定表为空＝假绿"
    assert mp.EXPLICIT_EXCLUSIONS, "排除表为空 ⇒ 0116/0062/0095 将非法"
