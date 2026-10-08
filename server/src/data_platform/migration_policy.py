# EXPAND-CONTRACT 破坏性迁移判定 —— 单一真源模块（批 111 产出 4）。
"""破坏性 DDL 两步走（expand → contract）的**唯一判定实现**（批 111，v2.3.1）。

## 职责（只有这一个实现，别处不得复刻判定逻辑）

1. **判定表**：upgrade 段内的回滚安全类 op（宽口径命中）+ 显式排除（真消费——
   宽口径先命中、排除项后洗白，删除任一排除项都会让对应迁移变红，见闸门反证钉⑤）。
2. **声明解析**：文件第 1 行 `# EXPAND-CONTRACT: ...`。
3. **受管集**：本版相对上一已部署版「新增 ∪ 内容变更」的迁移（sha256 指纹差集，
   非文件名差集——同名就地改同样落网，复审 P1-6）。
4. **部署门入口**：`check_release(new_dir, prev_dir)` 汇总退出码，CLI `--check-release` 消费。

## 两用约束

纯 stdlib、**不 import 同包任何模块**——既被 pytest `from src.data_platform.migration_policy
import ...` 导入，也被部署门当独立脚本跑（目标机 `shared/venv/bin/python`）。

## 退出码契约（bash 侧零映射、原样上浮；`failed_when` 用封闭式补集消费）

- `0` = 放行（无受管变更，或全部合规）
- `1` = 拒（stdout 含 `✗ 破坏性 DDL 命中: <file>` 行——`deploy/tests/run_scenarios.sh` S2 的判据接口，勿改文本）
- `2` = 用法或内部错（argparse 用法错误天然 exit 2）
- `3` = 不可判定（无上一版 / prev 目录无效 ⇒ 首部署，门跳过 + 显著告警）

## 已知边界（诚实声明）

- 只扫 `def upgrade` → `def downgrade` 段（downgrade 是合法回滚路径，不拦）；缺
  `def downgrade` 时截到文件尾。行级匹配：与排除项**同行**的破坏性 op 不拦（本仓
  无此形态；若未来出现须拆行或扩展为 AST 级）。
- 迁移文件视为不可变；alembic 之外的 SQL 手工执行不在门覆盖内。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 判定表（唯一真源；新增一类 op 只改这里）
# ---------------------------------------------------------------------------

# 回滚安全类（宽口径）：(正则, 类别, 为何危险)。
# ⚠ 宽口径是刻意的：`drop_(table|column|index|constraint)`、`DROP\s+\w+` 先宽命中，
#   再由 EXPLICIT_EXCLUSIONS 洗白放宽类——这样排除项才是**被真消费**的（反证钉⑤：
#   删掉 DROP CONSTRAINT 排除项 ⇒ 0116/0062 立即变红），而不是永远不触发的装饰。
ROLLBACK_UNSAFE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"DROP\s+(TABLE|COLUMN|INDEX)", "bare_sql_drop",
     "裸 SQL 删表/删列/删索引——数据不可逆丢失，旧代码读新 schema 直接崩"),
    (r"ALTER\s+COLUMN[^;\n]*\bTYPE\b", "alter_column_type",
     "ALTER COLUMN ... TYPE——类型收窄可截断/丢数据，回滚后旧代码读写新类型崩"),
    (r"op\.drop_(table|column|index)\s*\(", "op_drop",
     "alembic drop_table/drop_column/drop_index——同裸 SQL 删除"),
    (r"op\.drop_\w+\s*\(", "op_drop_wide",
     "alembic op.drop_* 兜底宽口径——新增 drop 类 op 默认进闸门，放宽类须显式排除"),
    (r"alter_column\s*\([^)]*type_\s*=", "op_alter_type",
     "alembic alter_column(type_=...)——类型变更"),
    (r"new_column_name\s*=", "op_rename_column",
     "alembic 列改名——旧代码读旧列名崩（2026-09-24 生产分裂事故当事 op）"),
    (r"\bRENAME\s+COLUMN\b", "bare_rename_column",
     "裸 SQL 列改名——同上"),
    (r"\bRENAME\s+TO\b", "bare_rename_table",
     "裸 SQL 表改名——旧代码读旧表名崩"),
    (r"op\.rename_table\s*\(", "op_rename_table",
     "alembic 表改名——同上"),
)

# 显式排除：(正则, 理由)。**放宽类 op 不破坏「旧代码可读新 schema」⇒ 与回滚安全无关**。
# ⚠ 后两条是本机制自洽的必要条件：0116（唯一两步走实例的 expand 侧）含 op.drop_constraint、
#   0062 upgrade 段含 op.drop_constraint——不排除则 P0-2 收紧后它们反成非法。
EXPLICIT_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    (r"DROP\s+CONSTRAINT", "放宽约束（删 CHECK/UNIQUE 不删数据）；不破坏旧代码读新 schema"),
    (r"op\.drop_constraint\s*\(", "同上（pythonic 形态；0116/0062 upgrade 段实例）"),
    (r"ALTER\s+COLUMN[^;\n]*\bSET\s+DEFAULT\b", "元数据放宽（0095 实例：SET DEFAULT 可回退，无数据重写）"),
)

_DECL_RE = re.compile(r"^#\s*EXPAND-CONTRACT:\s*(.*)$")
_UPGRADE_RE = re.compile(r"^def\s+upgrade\s*\(")
_DOWNGRADE_RE = re.compile(r"^def\s+downgrade\s*\(")


# ---------------------------------------------------------------------------
# 扫描 / 解析 / 判定
# ---------------------------------------------------------------------------

def scan_upgrade_section(text: str) -> str:
    """截 `def upgrade` → `def downgrade` 段；缺 downgrade 截到文件尾。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if _UPGRADE_RE.match(ln):
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _DOWNGRADE_RE.match(lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def _upgrade_body(text: str) -> str:
    """同 scan_upgrade_section 但**不含** `def upgrade` 行本身。"""
    seg = scan_upgrade_section(text)
    if not seg:
        return ""
    return seg.split("\n", 1)[1] if "\n" in seg else ""


def scan_unsafe_lines(text: str) -> list[tuple[int, str, str, str]]:
    """upgrade 段内的回滚安全类命中。

    行级算法：先对每行求显式排除（排除行整行豁免），再对剩余行匹配宽口径 unsafe。
    返回 [(行号(1-based, 全文坐标), 类别, 危险说明, 命中行文本)]。
    """
    seg = scan_upgrade_section(text)
    if not seg:
        return []
    seg_start_line = 1 + text[: text.index(seg)].count("\n")
    out: list[tuple[int, str, str, str]] = []
    for off, ln in enumerate(seg.splitlines()):
        stripped = ln.strip()
        if _UPGRADE_RE.match(stripped):
            continue  # def upgrade 行本身
        if any(re.search(rx, stripped) for rx, _ in EXPLICIT_EXCLUSIONS):
            continue
        for rx, kind, why in ROLLBACK_UNSAFE_PATTERNS:
            if re.search(rx, stripped):
                out.append((seg_start_line + off, kind, why, stripped))
                break
    return out


def parse_declaration(text: str) -> dict[str, str | bool | None]:
    """解析文件**第 1 行**的 `# EXPAND-CONTRACT: ...` 声明。

    返回 {"phase": str|None, "pair": str|None, "legacy": bool, "reason": str|None}。
    """
    decl: dict[str, str | bool | None] = {
        "phase": None, "pair": None, "legacy": False, "reason": None,
    }
    first = text.splitlines()[0] if text else ""
    m = _DECL_RE.match(first)
    if not m:
        return decl
    body = m.group(1)
    rm = re.search(r'reason\s*=\s*"([^"]*)"', body)
    if rm:
        decl["reason"] = rm.group(1)
        body = (body[: rm.start()] + body[rm.end():])
    for tok in body.split():
        if tok == "legacy":
            decl["legacy"] = True
        elif tok.startswith("phase="):
            decl["phase"] = tok[len("phase="):]
        elif tok.startswith("pair="):
            decl["pair"] = tok[len("pair="):]
    return decl


def evaluate(path: Path | str, chain_dir: Path | str,
             prev_dir: Path | str | None = None) -> tuple[bool, str]:
    """单文件判定。返回 (放行?, 原因)。

    `chain_dir`＝new_dir（本版迁移目录即全链，rsync 全量）。`prev_dir`＝上一**已部署**版
    迁移目录——提供时 pair 校验锚定它（pair 在 prev 存在且声明 expand ⇒ expand 已上产，
    「两步走要求跨发布」成立）；未提供（pytest 单文件场景）⇒ 回落 chain_dir 做存在性校验。
    """
    p = Path(path)
    chain = Path(chain_dir)
    name = p.name
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"{name}: 文件不可读（{e}）"
    decl = parse_declaration(text)
    hits = scan_unsafe_lines(text)

    if not hits:
        return True, "无回滚安全类命中"

    if decl["legacy"]:
        return False, (
            f"{name}: legacy 白名单文件有回滚安全类命中——legacy 仅限历史遗留（只减不增），"
            f"单文件判定不合规；⚠ 部署门上下文（check_release）对受管集内的 legacy 文件"
            f"分流为「可见不阻断」，合规性归仓内闸门④"
        )
    phase = decl["phase"]
    if phase is None:
        first_hit = hits[0]
        return False, (
            f"{name}: 未声明 EXPAND-CONTRACT 而命中回滚安全类 op（{first_hit[1]}：{first_hit[2]}）——"
            f"唯一合规声明＝两步走的 phase=contract（pair 指向已上产的 expand）"
        )
    if phase == "expand":
        return False, (
            f"{name}: phase=expand 不得携带回滚安全类 op（expand 只有「无命中」一种合法形态）——"
            f"破坏性 op 须拆到后续发布里的 contract 步"
        )
    if phase != "contract":
        return False, f"{name}: 未知 phase={phase!r}（合法值 expand/contract；历史遗留用 legacy）"
    pair = decl["pair"]
    if not pair:
        return False, f"{name}: phase=contract 须带 pair=<4 位 expand 迁移 id>"
    anchor = Path(prev_dir) if prev_dir is not None else chain
    where = "上一已部署版" if prev_dir is not None else "迁移目录"
    pair_files = sorted(anchor.glob(f"{pair}_*.py")) if anchor.is_dir() else []
    if not pair_files:
        return False, (
            f"{name}: pair={pair} 指向的 expand 迁移不在{where}——"
            f"两步走要求 expand 先行上产（跨发布），contract 不能与 expand 同发布捆绑"
        )
    pair_decl = parse_declaration(pair_files[0].read_text(encoding="utf-8"))
    if pair_decl["phase"] != "expand":
        hint = (
            "（该文件在已部署版无 expand 声明——若 expand 与本 contract 同为本次新增即同发布捆绑，两步走要求跨发布）"
            if prev_dir is not None else ""
        )
        return False, f"{name}: pair={pair} 在{where}存在但未声明 phase=expand{hint}"
    return True, f"合规 contract（pair={pair} 已上产且声明 expand）"


# ---------------------------------------------------------------------------
# 受管集（内容指纹差集）+ 部署门入口
# ---------------------------------------------------------------------------

def _digests(d: Path) -> dict[str, str] | None:
    if not d.is_dir():
        return None
    out: dict[str, str] = {}
    for f in sorted(d.glob("*.py")):
        out[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def managed_set(new_dir: Path | str, prev_dir: Path | str | None) -> tuple[list[str] | None, str]:
    """受管集＝新增 ∪ 内容变更（sha256 差集，非文件名差集）。

    返回 (受管文件名列表 | None, 说明)。None ⇒ 不可判定（prev 缺失/无效 ⇒ 首部署）。
    新版本目录无效 ⇒ raise NotADirectoryError（内部错，部署门 rc=2——与「首部署」
    的 3 严格分档：新面不存在是管道错误，prev 不存在是首次发布）。
    """
    new = _digests(Path(new_dir))
    if new is None:
        raise NotADirectoryError(f"新版本迁移目录不存在或不是目录: {new_dir}")
    if prev_dir is None:
        return None, "无上一版（首部署）"
    prev = _digests(Path(prev_dir))
    if prev is None:
        return None, f"上一版迁移目录不存在或不是目录: {prev_dir}"
    managed = sorted(n for n, h in new.items() if prev.get(n) != h)
    return managed, f"受管 {len(managed)} / 共 {len(new)} 个迁移"


def check_release(new_dir: Path | str, prev_dir: Path | str | None) -> tuple[int, list[str]]:
    """部署门入口。返回 (退出码 0/1/2/3, stdout 行列表)。"""
    try:
        managed, note = managed_set(new_dir, prev_dir)
    except OSError as e:
        return 2, [f"✗ 受管集计算内部错误: {e}"]
    if managed is None:
        return 3, [f"⚠ {note}——DDL 门不可判定，跳过（该窗口 prod 侧无门覆盖）"]
    lines = [note]
    if not managed:
        lines.append("（无新增/变更迁移——受管集为空）")
        return 0, lines
    new_path = Path(new_dir)
    prev_path = Path(prev_dir) if prev_dir is not None else None
    rejects: list[str] = []
    for name in managed:
        decl = parse_declaration((new_path / name).read_text(encoding="utf-8"))
        if decl["legacy"]:
            # 实现裁定（批 111 步 3，设计稿未覆盖的死锁出口）：legacy 白名单文件本批
            # 加声明行即内容变更 ⇒ 若拒则门开箱即拦死本批自己的上产。legacy 的合规性
            # 由仓内闸门 test_migration_expand_contract.py ④（双向校验+陈旧检测）守——
            # 契约闭合：部署门管「本版新增/变更的非 legacy 迁移」，仓内闸门管 legacy。
            # 此处保留可见性（不静默——复审 P1-6 的「不设防」至少留 stdout 痕迹）。
            lines.append(
                f"ℹ {name}: legacy 白名单文件新增/内容变更（已上产迁移不再执行；"
                f"历史不可变约定须人工确认，合规性归仓内闸门④）"
            )
            continue
        ok, why = evaluate(new_path / name, new_path, prev_path)
        if ok:
            lines.append(f"✓ {name}: {why}")
        else:
            rejects.append(f"✗ 破坏性 DDL 命中: {name}——{why}")
    if rejects:
        # 判据接口行前缀 `✗ 破坏性 DDL 命中: <file>`：S2/S7 沙箱以 grep "破坏性 DDL 命中"
        # 判门生效——勿改文本。所有拒均发生在有命中的前提下（evaluate 的各拒分支
        # 皆在 hits 非空之后），故该前缀对全部拒成立。
        lines.extend(rejects)
        return 1, lines
    return 0, lines


# ---------------------------------------------------------------------------
# CLI（唯一子命令；argparse 用法错误天然 exit 2）
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="migration_policy.py",
        description="破坏性 DDL 两步走部署门（批 111）：判定表与受管集的唯一实现",
    )
    ap.add_argument("--check-release", nargs="+", metavar="VERSIONS_DIR",
                    help="新版本迁移目录 [上一已部署版迁移目录]")
    args = ap.parse_args(argv)
    if not args.check_release:
        print("✗ 用法错误：须提供 --check-release <new_dir> [<prev_dir>]", file=sys.stderr)
        return 2
    new_dir = args.check_release[0]
    prev_dir = args.check_release[1] if len(args.check_release) > 1 else None
    if len(args.check_release) > 2:
        print("✗ 用法错误：--check-release 至多两个目录参数", file=sys.stderr)
        return 2
    rc, lines = check_release(new_dir, prev_dir)
    for ln in lines:
        print(ln)
    return rc


if __name__ == "__main__":
    sys.exit(main())
