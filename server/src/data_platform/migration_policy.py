# EXPAND-CONTRACT 破坏性迁移判定 —— 单一真源模块（批 111 产出 4）。
"""破坏性 DDL 两步走（expand → contract）的**唯一判定实现**（批 111，v2.3.1）。

## 职责（只有这一个实现，别处不得复刻判定逻辑）

1. **判定表**：upgrade 段内的回滚安全类 op（宽口径命中）+ 显式排除（**减法**——摘除被豁免
   片段后残句再匹配，豁免的是**形态**而非整语句）。反证钉⑤（删 `DROP CONSTRAINT` 两形态 ⇒
   0116/0062 变红）对**第 1/2 条**成立；**第 3 条**（`SET DEFAULT`）是**前瞻性声明**——当前
   判定表不命中该形态（删之全仓零变化），判定表扩展后即生效（实测见复审二同判 C3）。
2. **声明解析**：文件第 1 行 `# EXPAND-CONTRACT: ...`。
3. **受管集**：本版相对上一已部署版「新增 ∪ 内容变更」的迁移（sha256 指纹差集，
   非文件名差集——同名就地改同样落网，复审 P1-6）。
4. **冻结 legacy 白名单**（`LEGACY_FROZEN`）：**唯一真源在此**（测试从本模块 import，防
   「执行侧无清单」——步 4 同判 P0-1）。受管集内 legacy 文件二分流：**keying＝文件名**
   （与声明无关，P0-A）：名在冻结集 ⇒ 可见不阻断（历史遗留，且 upgrade 段须与已部署版
   **逐字一致**）；名不在 ⇒ 拒（新迁移禁标 legacy）。
5. **部署门入口**：`check_release(new_dir, prev_dir)` 汇总退出码，CLI `--check-release` 消费。

## 两用约束

纯 stdlib、**不 import 同包任何模块**——既被 pytest `from src.data_platform.migration_policy
import ...` 导入，也被部署门当独立脚本跑（目标机 `shared/venv/bin/python`）。

## 退出码契约（bash 侧零映射、原样上浮；`failed_when` 用封闭式补集消费）

- `0` = 放行（无受管变更，或全部合规）
- `1` = 拒（stdout 含 `✗ 破坏性 DDL 命中: <file>` 行——`deploy/tests/run_scenarios.sh` S2 的判据接口，勿改文本）
- `2` = 用法或内部错（argparse 用法错误天然 exit 2）
- `3` = 不可判定（无上一版 / prev 目录无效 ⇒ 首部署，门跳过 + 显著告警）

⚠ **`1` 的语义必须唯一＝「有命中且拒」**（步 4 同判 P0-2）：`failed_when` 的封闭式补集
把 `rc=1` 当作「命中」处理（`allow_contract` 时豁免阻断）⇒ 任何**内部异常**（含文件不可读、
编码非法）都**必须**映射为 `2`，**绝不落 `1`**——否则门崩溃会伪装成命中、被逃生门静默放行。
实现保证：`check_release` 的整段（含逐文件分类）包在 `except Exception` 内恒返 `2`。

## 已知边界（诚实声明）

- 只扫 `def upgrade` → `def downgrade` 段（downgrade 是合法回滚路径，不拦）；缺
  `def downgrade` 时截到文件尾。
- **排除判定＝「剥行内注释 → 按 `;` 切分语句 → 逐语句**减法**（摘除被豁免片段 → 残句再匹配）」**
  （步 4 P1-a 收窄到语句级；复审轮二 P0-C 再收窄到**片段级**）：不再整行/整句赦免 ⇒ 破坏性
  op 与排除项同行 / 同注 / 逗号并列 action 都不会被洗白。残留：
  ① `;` 落在**字符串字面量内**的多语句 SQL 会被切碎——无害（碎片仍各自匹配，破坏性 op 仍被捕获）；
  ② **SQL 行内注释（`--` / `/* */`）位于字符串字面量内 ⇒ 不剥、参与匹配**（fail-closed：注释里
  出现判定表关键词会误报红，方向安全）；
  ③ 排除项 #3 摘除的是 `SET DEFAULT <值至分隔符>` ⇒ 若同一语句在**无分隔符**处紧跟另一个
  action（非合法多 action SQL），该 action 会随默认值一并被摘除。
- 迁移文件视为不可变：`check_release` 对**冻结 legacy** 另比 `upgrade` 段与**上一已部署版**
  逐字一致（防「改已部署迁移的函数体」）。**逐字＝含注释与空白**（改一个字符即拒——
  fail-closed 方向，非假红）。残留：**`downgrade` 段与 docstring** 不参与该比对
  （downgrade 是合法回滚路径，不在门覆盖内）。
- 冻结集校验的 keying 是**文件名**、**不读声明**（P0-A）⇒ 冻结集内文件无论头写 `legacy`／
  `phase=contract`／无声明，一律逐字比对 `upgrade` 段。
- **匹配大小写不敏感**（`_MATCH_FLAGS = re.IGNORECASE`，判定表与排除表共用）——SQL 关键字
  与 alembic op 名大小写不敏感，规范 §1.1 的对象是**语句本身**（P0-B）。
- `legacy` 与 `phase`/`pair` **互斥**（同现即拒）——防「一行声明同时拿到双通道」。
- alembic 之外的 SQL 手工执行不在门覆盖内。
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

# 匹配旗标（**两表共用，单一处**）：SQL 关键字与 alembic op 名大小写不敏感 ⇒
# 规范 §1.1 声明的对象是**语句本身**，`drop table t` 与 `DROP TABLE t` 必须同判
# （步 4 复审轮② P0-B：大小写敏感 ⇒ 一整类等价破坏性语句对 prod 唯一机械强制点隐形）。
_MATCH_FLAGS = re.IGNORECASE

# 回滚安全类（宽口径）：(正则, 类别, 为何危险)。
# ⚠ 宽口径是刻意的（**禁枚举式 deny-list——枚举必漂**）：裸 SQL `DROP\s+\w+` 与 pythonic
#   `op.drop_\w+(` 一律**按对象类型无关**宽命中，再由 EXPLICIT_EXCLUSIONS 洗白放宽类——
#   这样排除项才是**被真消费**的（反证钉⑤：删掉 DROP CONSTRAINT 排除项 ⇒ 0116/0062 立即
#   变红），且未列对象（SCHEMA/VIEW/TRIGGER/FUNCTION…）默认落网而非静默放行。
ROLLBACK_UNSAFE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"DROP\s+\w+", "bare_sql_drop",
     "裸 SQL DROP <任意对象>——删表/删列/删索引等数据不可逆丢失，旧代码读新 schema 直接崩；"
     "不枚举对象类型 ⇒ 未列对象（SCHEMA/VIEW/TRIGGER…）默认落网，放宽类由排除项洗白"),
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
# ⚠ 匹配同用 `_MATCH_FLAGS`（大小写不敏感）——否则「宽口径先命中、排除项后洗白」在
#   小写拼写下断裂（写 `drop constraint` 既没被宽口径命中、也没被排除，实为放行）。
EXPLICIT_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    (r"DROP\s+CONSTRAINT", "放宽约束（删 CHECK/UNIQUE 不删数据）；不破坏旧代码读新 schema"),
    (r"op\.drop_constraint\s*\(", "同上（pythonic 形态；0116/0062 upgrade 段实例）"),
    # ⚠ 豁免的是 **SET DEFAULT 这个 action 本身**（连同其默认值表达式，至分隔符为止），
    #   而**不摘除** `ALTER COLUMN <列名>` 锚点——理由（复审轮四 P1-F/P1-G，均有实测）：
    #   ① 写成 `ALTER COLUMN <列名> SET DEFAULT` 并要求列名匹配 `\w+`/`[^\s,;]+` ⇒ 引号或
    #      schema 限定的列名失配 ⇒ §1.3 明文豁免的 SET DEFAULT 反被判红（假红）；
    #      且用 `"ALTER COLUMN"` 作替换串会把默认值里的 `TYPE` 变成 `ALTER COLUMN 'TYPE'`（仍假红）。
    #   ② 写成 `[^;\n]*` 会贪婪跨越分隔符 ⇒ 吃掉同语句另一个 §1.1 明列 op（P0-D）。
    #   ③ 摘除时**连锚点一起删** ⇒ 残句失去 `ALTER COLUMN` ⇒ 后续 `, TYPE …` 失配（减法残留洞）。
    #   ⇒ 只摘 `SET DEFAULT <值>`、保留锚点，三者同时闭合（16 例语料全达标、全仓 16/33 不变）。
    (r"\bSET\s+DEFAULT\b[^,;]*", "元数据放宽（0095 实例：SET DEFAULT 可回退，无数据重写）；"
     "豁免的只是 SET DEFAULT 这个 action 及其默认值——`ALTER COLUMN <列名>` 锚点保留，"
     "使同一语句的其它 action 继续被判定"),
)

# ---------------------------------------------------------------------------
# 冻结 legacy 白名单（**唯一真源**——测试从本模块 import，不得在别处复刻）
# ---------------------------------------------------------------------------
#
# 语义：`legacy` 声明＝「历史遗留、纪律之前上产的破坏性迁移」。**只减不增**——新增一项
# 须过用户裁定并同步改本常量。
#
# 为何必须有这张表：批 111 步 4 同判 P0-1——`legacy` 是自声明标记，若部署门仅凭标记放行，
# 则「新迁移标 legacy」＝ 一条**比 allow_contract 更易达、且零留痕**的绕过通道（prod 唯一
# 机械强制点被一行注释绕过）。故门须**验证**：名在冻结集内 ⇒ 历史遗留（放行为「可见不阻
# 断」）；名不在 ⇒ 拒。
LEGACY_FROZEN: frozenset[str] = frozenset({
    "0010_simplify_llm_gateway.py",
    "0012_llm_token_limits.py",
    "0042_schema_consolidate.py",
    "0052_drop_feishu_config.py",
    "0070_drop_email_verified.py",
    "0085_drop_channel_config.py",
    "0088_llm_position_budget_drop.py",
    "0090_external_interface.py",
    "0096_retire_self_collected_minute.py",
    "0100_venue_backfill.py",
    "0101_retire_strategy_account.py",
    "0102_retire_live_task_account_id.py",
    "0104_rename_venue_to_account.py",
    "0111_m6_retire.py",
    "0114_drop_shadow_quality.py",
})

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


def _strip_comment(line: str) -> str:
    """去掉行内注释：仅当 `#` 位于字符串字面量**之外**才视为注释起点。逐字符扫描、不用正则。"""
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote is not None:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def scan_unsafe_lines(text: str) -> list[tuple[int, str, str, str]]:
    """upgrade 段内的回滚安全类命中。

    行内算法：**先剥行内注释（字符串外的 `#`），再按 `;` 切分语句，逐语句**先做显式排除的
    **减法**（摘除被豁免片段），再对**残句**匹配宽口径 unsafe。**既不整行、也不整句赦免**
    （步 4 同判 P1-a：语句级；复审轮二 P0-C：片段级）——与排除项同行/同注/逗号并列的破坏性
    op 不得静默放行。

    判定表与排除表**一律按 `_MATCH_FLAGS`（`re.IGNORECASE`）匹配**（步 4 复审轮② P0-B）。
    返回 [(行号(1-based, 全文坐标), 类别, 危险说明, 命中语句文本（**原始**语句，非残句）)]。
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
        code = _strip_comment(stripped).strip()
        if not code:
            continue
        for stmt in code.split(";"):
            raw = stmt.strip()
            if not raw:
                continue
            # P0-C：排除项按**减法**处理——只摘除被豁免的**片段**，再对残句匹配判定表。
            # 旧实现「语句含排除项关键词 ⇒ 整句跳过」：关键词出现在 SQL 注释（`--`/`/* */`）
            # 或逗号并列的其它 action 中时，同语句的破坏性 op 被**静默洗白**——且是本轮
            # `re.IGNORECASE` 引入的**回归**（`DROP TABLE old_t -- drop constraint later`
            # 旧命中 1 ⇒ 新 0）。豁免的是**形态**，不是**语句**（规范 §1.3）。
            s = raw
            for rx, _ in EXPLICIT_EXCLUSIONS:
                s = re.sub(rx, " ", s, flags=_MATCH_FLAGS)
            for rx, kind, why in ROLLBACK_UNSAFE_PATTERNS:
                if re.search(rx, s, _MATCH_FLAGS):
                    out.append((seg_start_line + off, kind, why, raw))
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

    ⚠ 文件不可读/编码非法时**抛出** `OSError`/`UnicodeDecodeError`（不再吞成「拒」——否则
    门崩溃会伪装成「命中」并被逃生门豁免，见模块头「退出码契约 ⚠」）。部署门侧由
    `check_release` 统一映射为 `rc=2`。

    `chain_dir`＝new_dir（本版迁移目录即全链，rsync 全量）。`prev_dir`＝上一**已部署**版
    迁移目录——提供时 pair 校验锚定它（pair 在 prev 存在且声明 expand ⇒ expand 已上产，
    「两步走要求跨发布」成立）；未提供（pytest 单文件场景）⇒ 回落 chain_dir 做存在性校验。
    """
    p = Path(path)
    chain = Path(chain_dir)
    name = p.name
    text = p.read_text(encoding="utf-8")
    decl = parse_declaration(text)
    hits = scan_unsafe_lines(text)

    if decl["legacy"] and (decl["phase"] or decl["pair"]):
        return False, f"{name}: legacy 与 phase/pair 不得同现（互斥声明）"

    if not hits:
        return True, "无回滚安全类命中"

    if decl["legacy"]:
        return False, (
            f"{name}: legacy 白名单文件有回滚安全类命中——legacy 仅限历史遗留（只减不增）；"
            f"单文件判定不合规。⚠ 部署门（check_release）仅对**冻结集内** legacy 分流"
            f"「可见不阻断」，非冻结 legacy 直接拒；合规性归仓内闸门④"
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


def _frozen_legacy_unchanged(name: str, new_path: Path,
                             prev_path: Path | None) -> tuple[bool, str]:
    """冻结 legacy 的终局校验：`upgrade` 段须与上一已部署版**逐字一致**。

    冻结集只保证「名字是历史遗留」，**不保证内容没被改过**（步 4 同判 P0-1 对「门仅凭
    标记放行」的批评）。声明行在第 1 行、位于 `upgrade` 段之外 ⇒ 加声明行不影响本比对。
    """
    if prev_path is None:
        return True, "冻结 legacy（无上一版可比）"
    prev_file = prev_path / name
    if not prev_file.is_file():
        return False, "冻结 legacy 不在上一已部署版（历史遗留文件必已上产——矛盾）"
    if scan_upgrade_section((new_path / name).read_text(encoding="utf-8")) != \
            scan_upgrade_section(prev_file.read_text(encoding="utf-8")):
        return False, "冻结 legacy 的 upgrade 段相对已部署版发生变化（违反迁移不可变约定）"
    return True, "冻结 legacy 且 upgrade 段与已部署版一致"


def _classify_managed(new_dir: Path | str, prev_dir: Path | str | None,
                      managed: list[str], note: str) -> tuple[list[str], list[str]]:
    """逐文件分类受管集（纯内部；所有异常上浮给 check_release 映射为 rc=2）。"""
    lines = [note]
    if not managed:
        lines.append("（无新增/变更迁移——受管集为空）")
        return lines, []
    new_path = Path(new_dir)
    prev_path = Path(prev_dir) if prev_dir is not None else None
    rejects: list[str] = []
    for name in managed:
        text = (new_path / name).read_text(encoding="utf-8")
        decl = parse_declaration(text)
        if decl["legacy"] and (decl["phase"] or decl["pair"]):
            rejects.append(f"✗ 破坏性 DDL 命中: {name}——legacy 与 phase/pair 不得同现（互斥声明）")
            continue
        # P0-A（步 4 复审轮②）：冻结集的 keying 是「**名在冻结集内**」（规范 §5.1 原文），
        # **与自声明解耦**——触发条件不得读 `decl["legacy"]`，否则「body 篡改 + 头重标
        # phase=contract pair=<已上产 expand>」即旁路本比对（实测 rc=0，见同判② 分歧 2）。
        if name in LEGACY_FROZEN:
            ok, why = _frozen_legacy_unchanged(name, new_path, prev_path)
            if ok:
                lines.append(f"ℹ {name}: {why}（已上产迁移，不阻断；合规性归仓内闸门④）")
            else:
                rejects.append(f"✗ 破坏性 DDL 命中: {name}——{why}")
            continue
        if decl["legacy"]:
            # 步 4 同判 P0-1：legacy 是**自声明**标记 ⇒ 名不在冻结集一律拒（门**验证**非信任）。
            if scan_unsafe_lines(text):
                rejects.append(
                    f"✗ 破坏性 DDL 命中: {name}——非冻结 legacy（legacy 只减不增；"
                    f"新迁移禁标 legacy——规范硬规则①）+ 破坏性 op"
                )
            else:
                lines.append(
                    f"ℹ {name}: 标记 legacy 但无破坏性 op（非冻结，不阻断；合规性归仓内闸门④）"
                )
            continue
        ok, why = evaluate(new_path / name, new_path, prev_path)
        if ok:
            lines.append(f"✓ {name}: {why}")
        else:
            rejects.append(f"✗ 破坏性 DDL 命中: {name}——{why}")
    return lines, rejects


def check_release(new_dir: Path | str, prev_dir: Path | str | None) -> tuple[int, list[str]]:
    """部署门入口。返回 (退出码 0/1/2/3, stdout 行列表)。

    ⚠ **整段包在 `except Exception` 内 ⇒ 任何非预期异常恒返 2**（门不可用），**绝不落 `1`**
    （步 4 同判 P0-2：`1` 的语义必须唯一＝「有命中且拒」；否则门崩溃（如迁移文件编码非法
    ⇒ `UnicodeDecodeError`，属 `ValueError` 非 `OSError`）会伪装成命中，被 `allow_contract`
    当「命中豁免」静默放行）。`2` 不在封闭式补集的放行集内 ⇒ 恒红。
    """
    try:
        managed, note = managed_set(new_dir, prev_dir)
        if managed is None:
            return 3, [f"⚠ {note}——DDL 门不可判定，跳过（该窗口 prod 侧无门覆盖）"]
        lines, rejects = _classify_managed(new_dir, prev_dir, managed, note)
    except Exception as e:  # 门须 fail-closed：任何异常＝不可判定＝恒红（不当成「命中」，也不放行）
        return 2, [
            f"✗ DDL 门内部错误（不可判定，恒红——即使 allow_contract）: {type(e).__name__}: {e}"
        ]
    if rejects:
        # 判据接口行前缀 `✗ 破坏性 DDL 命中: <file>`：S2/S7 沙箱以 grep "破坏性 DDL 命中"
        # 判门生效——勿改文本。所有拒均发生在有命中的前提下（各拒分支皆在 hits 非空之后）。
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
