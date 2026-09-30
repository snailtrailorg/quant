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

## 钉三段

1. **表名一致性**（`deploy/wrappers/`）：SQL 里 `FROM/INTO/UPDATE/JOIN <t>` 的表名必须命中
   `schema_expectations.txt`（列集合真源，迁移链生成物）。表被拆/改名后旧名不在其中即红。
   wrapper 是 bash，SQL 面极小且**无 Python `from X import` 撞名**，可安全枚举；
   `server/scripts/*.py` 含大量 `from … import`，故**不**对其实施本法（改用第 2 段的字面量法）。
2. **退役 artifact 字面量**（`deploy/**` + `server/scripts/**`）：`external_interface`（83a 已 DROP 的
   表）与 `ANY(capabilities)`（退役域谓词——拆表后「表本身即域」）不得再出现在 **SQL 语句行**里。
   只扫「含 SQL 关键字且非注释」的行：注释/文档串里提历史表名是**合法**的（如「批 83a：
   external_interface 拆 data_source + trading_account」），不构成回归。
3. **真源在效**：`schema_expectations.txt` 必须存在且非空——第 1 段赖以成立的基线不能退化成空集假绿。

**覆盖边界（诚实声明）**：本闸门只保证「**部署链的表名/退役谓词不漂移**」，不覆盖 SQL 语义
（列名、占位符形态、JOIN 正确性）——那类靠真库往返钉。也不扫 `server/migrations/`（迁移**本就**
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

# 已退役 artifact：出现（于 SQL 行）即回归
_RETIRED = {
    "external_interface": "批 83a 已 DROP 的表（拆为 data_source + trading_account）",
    "ANY(capabilities)": "退役的域谓词——拆表后「表本身即域」，勿再按 capabilities 过滤域",
}

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


def _sql_lines(path: Path):
    """产出 (行号, 文本)：跳过 `#` 整行注释，要求行内含 SQL 关键字。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    for i, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        if _SQL_LINE_RE.search(raw):
            yield i, raw


@pytest.mark.skipif(not _WRAPPERS.is_dir(), reason="无 deploy/wrappers（非本仓布局）")
def test_wrapper_sql_tables_exist_in_schema():
    """**本闸门核心**：deploy/wrappers/ 的 SQL 表名必须存在于 schema（拆表漏改即红）。"""
    tables = _schema_tables()
    offenders = []
    for path in _iter_files(_WRAPPERS):
        for ln, line in _sql_lines(path):
            for tbl in _TABLE_REF_RE.findall(line):
                if tbl.lower() not in tables:
                    offenders.append(f"{path.relative_to(_REPO)}:{ln}: 表 `{tbl}` 不在 schema → {line.strip()[:90]}")
    assert not offenders, (
        "deploy/wrappers 的 SQL 引用了 schema 中不存在的表（拆表/改名后 wrapper 漏改？）:\n  "
        + "\n  ".join(offenders)
        + "\n→ 这些是服务器侧特权脚本（bootstrap.yml 装位），pytest/src 扫描看不见，"
          "拆表类改动必须显式扫部署链")


@pytest.mark.parametrize("token,reason", sorted(_RETIRED.items()))
def test_retired_artifacts_absent_from_deploy_chain(token: str, reason: str):
    """退役 artifact 不得再出现在 deploy/** 与 server/scripts/** 的 **SQL 行**里。"""
    offenders = []
    for root in (_DEPLOY, _SERVER_SCRIPTS):
        if not root.is_dir():
            continue
        for path in _iter_files(root):
            for ln, line in _sql_lines(path):
                if token in line:
                    offenders.append(f"{path.relative_to(_REPO)}:{ln}: {line.strip()[:100]}")
    assert not offenders, (
        f"退役 artifact `{token}`（{reason}）重现于部署链 SQL:\n  "
        + "\n  ".join(offenders)
        + "\n→ 注释/文档里提历史表名合法；但 SQL 语句里出现即回归（对照 83a 拆表的 2 个 wrapper 事故）")


def test_schema_expectations_source_is_alive():
    """第 1 段的真源必须非空（否则闸门退化成空集假绿）。"""
    assert _SCHEMA_EXP.is_file(), f"缺 schema 真源：{_SCHEMA_EXP}"
    assert len(_schema_tables()) > 0, "schema_expectations.txt 解析出 0 张表——真源失效，表名闸门形同虚设"
