"""拆表/改表影响面闸门：deploy/ 运维 SQL 的表名必须存在于 schema（批 83a 盲区教训）。

## 为什么需要这条闸门

批 83a 拆表（`external_interface` → `data_source` + `trading_account`）的**审核盲区**：
影响面只扫了 `server/src` + `web/src`，**漏了 `deploy/wrappers/` 里的运维 SQL**。
staging 彩排 preflight（阶段 1 `quant-dbro hub`）当场撞
`ERROR: relation "external_interface" does not exist` → 部署拦停（阻断级）。

这些 wrapper 是**服务器侧特权脚本**（`/usr/local/sbin`，`bootstrap.yml` 一次性 root 级装位，
不进常态 release 管道）——pytest 套件、`server/src` 扫描、前端构建**全都看不见它们**。
于是「表被拆/改名」这类改动的读点清单必须**显式包含部署链**，否则同类回归**无任何东西会响**。

同类工具面：`server/scripts/`（随 rsync 上服务器的运维脚本，如 `verify_integration.py`）
同样是 pytest 视野外的读点——本例中它也残留了退役域谓词（`'trading' = ANY(capabilities)`）。

## 钉四段

1. **表名一致性**（`deploy/wrappers/`）：SQL 里 `FROM/INTO/UPDATE/JOIN <t>` 的表名必须命中
   `schema_expectations.txt`（列集合真源，迁移链生成物）。表被拆/改名后旧名不在其中即红。
   wrapper 是 bash，SQL 面极小且**无 Python `from X import` 撞名**，可安全枚举；
   `server/scripts/*.py` 含大量 `from … import`，故**不**对其实施本法（改用第 2 段的字面量法）。
2. **退役 artifact 字面量**（`deploy/**` + `server/scripts/**`）：`external_interface`（83a 已 DROP 的
   表）与 `ANY(capabilities)`（退役域谓词——拆表后「表本身即域」）不得再出现在 **SQL 语句行**里。
   只扫「含 SQL 关键字且非注释」的行：注释/文档串里提历史表名是**合法**的（如「批 83a：
   external_interface 拆 data_source + trading_account」），不构成回归。
   **例外＝第 4 段的哨兵行**（显式声明的过渡期兼容分支）。
3. **真源在效**：`schema_expectations.txt` 必须存在且非空——第 1 段赖以成立的基线不能退化成空集假绿。
4. **过渡期哨兵纪律（`TRANSITION-0115`）**：装位件不随 release 版本化，而 83a 的 `0116` 是
   **contract 型迁移**（DROP 旧表），且 `quant-dbro hub` 在 alembic **之前**跑、`quant-hbcheck` 在
   **之后**跑（阶段 8 postverify，`failed_when: rc != 0`）——单态 wrapper **无装位时序可解**：
   先装查新表 → preflight 撞未建的表拦停；不装 → postverify 撞已删的旧表失败，而回滚**不回退 schema**
   → 旧代码 + 已删表 = 撕裂态（2026-09-30 对 prod 实查后定论）。故过渡期**必须**双态。
   代价要**被声明、有界、可检测**，而不是悄悄留在代码里：
   - 旧态 SQL 行须带哨兵——两形态：**行内**（该行出现 `TRANSITION-0115`）或**域内**
     （被 `TRANSITION-0115:BEGIN` / `:END` 包住的整段，用于跨行 SQL 串），且该行引用的非
     schema 表只许是 `external_interface`；
   - 带哨兵的文件须在文件头写明 `TRANSITION-0115:收口=<条件>`（非空）——不写声明即红；
   - **成对**：带哨兵的文件须同时存在新表形态（`trading_account`）——防只留旧分支；
   - **残留也红**：声明还在但已无哨兵行（收口后忘删声明）同样报错——逼收口动作闭环；
   - 哨兵只许出现在 `deploy/wrappers/`（装位件目录），不得扩散到其它部署链文件。

**覆盖边界（诚实声明）**：本闸门只保证「**部署链的表名/退役谓词不漂移 + 过渡期哨兵有据**」，
不覆盖 SQL 语义（列名、占位符形态、JOIN 正确性）——那类靠真库往返钉（本例：两态 scratch schema
跑真 wrapper 的回归已人工执行，见提交信息）。也不扫 `server/migrations/`（迁移**本就**
引用历史表名，如 0116 的 DROP external_interface）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_WRAPPERS = _REPO / "deploy" / "wrappers"
_DEPLOY = _REPO / "deploy"
_SERVER_SCRIPTS = _REPO / "server" / "scripts"
_SCHEMA_EXP = _REPO / "server" / "src" / "data_platform" / "schema_expectations.txt"

# 表引用：FROM/INTO/UPDATE/JOIN <ident>（wrapper 为 bash——无 Python `from … import` 撞名）
_TABLE_REF_RE = re.compile(r"\b(?:from|into|update|join)\s+([a-z_][a-z0-9_]*)\b", re.IGNORECASE)

# SQL 语句行判据（排除注释/文档串/散文里的历史提法）
_SQL_LINE_RE = re.compile(r"\b(select|insert|update|delete|from|where|join|into)\b", re.IGNORECASE)

# 已退役 artifact：出现（于 SQL 行）即回归——除哨兵行外
_RETIRED = {
    "external_interface": "批 83a 已 DROP 的表（拆为 data_source + trading_account）",
    "ANY(capabilities)": "退役的域谓词——拆表后「表本身即域」，勿再按 capabilities 过滤域",
}

# ---- 第 4 段：过渡期哨兵（双态兼容分支）----
_TRANSITION_SENTINEL = "TRANSITION-0115"
# 文件头声明形态：`TRANSITION-0115:收口=<非空条件>`
_TRANSITION_DECL_RE = re.compile(r"TRANSITION-0115:收口=(\S+)")
# 哨兵行上唯一允许引用的「非 schema 表」（即已知旧名——写错别的表名照样红）
_SENTINEL_ALLOWED_TABLES = frozenset({"external_interface"})

# 扫描时跳过的目录（第三方/缓存/日志）
_SKIP_PARTS = {".venv", "__pycache__", "node_modules", ".git"}
_SKIP_SUFFIX = {".pyc", ".log"}


def _schema_tables() -> set[str]:
    """`schema_expectations.txt` 行形如 `table :: col1,col2,…` → 表名集合。"""
    tables: set[str] = set()
    for line in _SCHEMA_EXP.read_text(encoding="utf-8").splitlines():
        if "::" in line:
            tables.add(line.split("::", 1)[0].strip())
    return tables


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        if path.suffix in _SKIP_SUFFIX:
            continue
        yield path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _sql_lines(path: Path):
    """产出 (行号, 文本)：跳过 `#` 整行注释，要求行内含 SQL 关键字。"""
    for i, raw in enumerate(_read(path).splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        if _SQL_LINE_RE.search(raw):
            yield i, raw


def _sentinel_marked_lines(path: Path) -> tuple[set[int], list[str]]:
    """产出 (带哨兵的行号集合, 纪律错误)。

    哨兵两形态：**行内**（该行文本含 `TRANSITION-0115`）或**域内**（处于
    `TRANSITION-0115:BEGIN` … `TRANSITION-0115:END` 之间——跨行 SQL 串用）。
    域不成对（`END` 无 `BEGIN` / `BEGIN` 未闭合 / `BEGIN` 嵌套）即报错。
    """
    marked: set[int] = set()
    errs: list[str] = []
    depth = 0
    for i, raw in enumerate(_read(path).splitlines(), 1):
        if f"{_TRANSITION_SENTINEL}:BEGIN" in raw:
            if depth:
                errs.append(f"{path.name}:{i}: 哨兵 BEGIN 嵌套/重复")
            depth += 1
            marked.add(i)
            continue
        if f"{_TRANSITION_SENTINEL}:END" in raw:
            depth -= 1
            if depth < 0:
                errs.append(f"{path.name}:{i}: 哨兵 END 无匹配的 BEGIN")
                depth = 0
            marked.add(i)
            continue
        if _TRANSITION_SENTINEL in raw or depth > 0:
            marked.add(i)
    if depth > 0:
        errs.append(f"{path.name}: 哨兵 BEGIN 未闭合")
    return marked, errs


@pytest.mark.skipif(not _WRAPPERS.is_dir(), reason="无 deploy/wrappers（非本仓布局）")
def test_wrapper_sql_tables_exist_in_schema():
    """**本闸门核心**：deploy/wrappers/ 的 SQL 表名必须存在于 schema（拆表漏改即红）。

    例外：带哨兵的旧态行（见第 4 段）——且其上只许是已知旧表名。
    """
    tables = _schema_tables()
    offenders = []
    for path in _iter_files(_WRAPPERS):
        marked_lines, _ = _sentinel_marked_lines(path)
        for ln, line in _sql_lines(path):
            marked = ln in marked_lines
            for tbl in _TABLE_REF_RE.findall(line):
                if tbl.lower() in tables:
                    continue
                if marked and tbl.lower() in _SENTINEL_ALLOWED_TABLES:
                    continue
                why = "哨兵行/域上出现非已知旧表名" if marked else "表不在 schema"
                offenders.append(
                    f"{path.relative_to(_REPO)}:{ln}: 表 `{tbl}` {why} → {line.strip()[:90]}"
                )
    assert not offenders, (
        "deploy/wrappers 的 SQL 引用了 schema 中不存在的表（拆表/改名后 wrapper 漏改？）:\n  "
        + "\n  ".join(offenders)
        + "\n→ 这些是服务器侧特权脚本（bootstrap.yml 装位），pytest/src 扫描看不见，"
          "拆表类改动必须显式扫部署链。过渡期双态须走哨兵纪律（第 4 段）而非硬留旧表名"
    )


@pytest.mark.parametrize("token,reason", sorted(_RETIRED.items()))
def test_retired_artifacts_absent_from_deploy_chain(token: str, reason: str):
    """退役 artifact 不得再出现在 deploy/** 与 server/scripts/** 的 **SQL 行**里（哨兵行除外）。"""
    offenders = []
    for root in (_DEPLOY, _SERVER_SCRIPTS):
        if not root.is_dir():
            continue
        for path in _iter_files(root):
            marked_lines, _ = _sentinel_marked_lines(path)
            for ln, line in _sql_lines(path):
                if token in line and ln not in marked_lines:
                    offenders.append(f"{path.relative_to(_REPO)}:{ln}: {line.strip()[:100]}")
    assert not offenders, (
        f"退役 artifact `{token}`（{reason}）重现于部署链 SQL:\n  "
        + "\n  ".join(offenders)
        + "\n→ 注释/文档里提历史表名合法；但 SQL 语句里出现即回归（对照 83a 拆表的 2 个 wrapper 事故）。"
          "确需过渡期双态 → 按第 4 段打哨兵并写收口声明"
    )


def test_schema_expectations_source_is_alive():
    """第 1 段的真源必须非空（否则闸门退化成空集假绿）。"""
    assert _SCHEMA_EXP.is_file(), f"缺 schema 真源：{_SCHEMA_EXP}"
    assert len(_schema_tables()) > 0, "schema_expectations.txt 解析出 0 张表——真源失效，表名闸门形同虚设"


def test_transition_sentinel_is_declared_and_paired():
    """第 4 段：哨兵须**声明 + 成对**，且收口后残留的声明同样报错（逼闭环）。"""
    errs: list[str] = []

    for path in _iter_files(_WRAPPERS):
        text = _read(path)
        rel = path.relative_to(_REPO)
        decls = _TRANSITION_DECL_RE.findall(text)
        marked_lines, span_errs = _sentinel_marked_lines(path)
        errs.extend(span_errs)
        marked = [(ln, line) for ln, line in _sql_lines(path) if ln in marked_lines]

        if marked and not decls:
            errs.append(
                f"{rel}: {len(marked)} 行带哨兵 `{_TRANSITION_SENTINEL}` 但缺文件头声明 "
                f"`{_TRANSITION_SENTINEL}:收口=<条件>`（→ 须写明收口条件）"
            )
        if decls and not marked:
            errs.append(
                f"{rel}: 声明 `{_TRANSITION_SENTINEL}:收口=` 仍在但已无哨兵行 —— "
                "收口后残留的过期声明，须删除（stale declaration）"
            )
        if marked and "trading_account" not in text:
            errs.append(
                f"{rel}: 有旧态哨兵行但文件内无新表形态 `trading_account` —— 兼容分支不成对"
                "（只留旧分支＝把兼容当常态）"
            )

    # 哨兵只许出现在装位件目录（deploy/wrappers/），不得扩散到其它部署链文件
    for root in (_DEPLOY, _SERVER_SCRIPTS):
        if not root.is_dir():
            continue
        for path in _iter_files(root):
            if _WRAPPERS in path.parents:
                continue
            if _TRANSITION_SENTINEL in _read(path):
                errs.append(
                    f"{path.relative_to(_REPO)}: 哨兵 `{_TRANSITION_SENTINEL}` 只允许出现在 "
                    "deploy/wrappers/（装位件）——其它位置的旧表名引用一律按回归处理"
                )

    assert not errs, "过渡期哨兵纪律违规:\n  " + "\n  ".join(errs)
