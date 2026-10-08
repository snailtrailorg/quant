"""migration_policy.py 单元测试（批 111 产出 5·test_migration_policy）。

纯函数级：判定分类 / 显式排除真生效 / 声明解析 / upgrade 段截断 /
受管集内容指纹 / CLI 退出码 0/1/2/3 / 清单反向校验（守卫自身的守卫）。
不连 DB、不跑迁移。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.data_platform.migration_policy import (
    EXPLICIT_EXCLUSIONS,
    ROLLBACK_UNSAFE_PATTERNS,
    check_release,
    evaluate,
    managed_set,
    parse_declaration,
    scan_unsafe_lines,
    scan_upgrade_section,
)

_MODULE = Path(__file__).resolve().parents[1] / "src" / "data_platform" / "migration_policy.py"
_VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _mig(decl: str = "", upgrade: str = 'op.execute("SELECT 1")') -> str:
    head = f'{decl}\n"""docstring 保持成立"""\n' if decl else '"""docstring"""\n'
    return (
        head
        + 'revision = "0099"\n\n\n'
        + "def upgrade() -> None:\n    from alembic import op\n\n    "
        + upgrade
        + "\n\n\ndef downgrade() -> None:\n    pass\n"
    )


# ---------------------------------------------------------------------------
# 判定分类
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        'op.execute("DROP TABLE old_t")',
        'op.execute("DROP COLUMN c")',
        'op.execute("DROP INDEX idx")',
        'op.execute("ALTER TABLE t ALTER COLUMN c TYPE varchar(8)")',
        "op.drop_table('t')",
        "op.drop_column('t', 'c')",
        "op.drop_index('ix')",
        "op.alter_column('t', 'c', type_=sa.String(8))",
        "op.alter_column('t', 'c', existing_type=sa.Integer(), new_column_name='c2')",
        'op.execute("ALTER TABLE t RENAME COLUMN a TO b")',
        'op.execute("ALTER TABLE t RENAME TO t2")',
        "op.rename_table('t', 't2')",
    ],
)
def test_rollback_unsafe_ops_hit(body: str) -> None:
    hits = scan_unsafe_lines(_mig(upgrade=body))
    assert hits, f"须命中: {body}"
    kind, why = hits[0][1], hits[0][2]
    assert kind and why  # 类别与「为何危险」齐备


def test_renames_hit_not_silent() -> None:
    """RENAME 三形态全命中——2026-09-24 生产分裂事故当事 op 不得再是盲区。"""
    for body in (
        "op.alter_column('t', 'c', new_column_name='c2')",
        'op.execute("ALTER TABLE t RENAME COLUMN a TO b")',
        "op.rename_table('a', 'b')",
    ):
        assert scan_unsafe_lines(_mig(upgrade=body)), body


# ---------------------------------------------------------------------------
# 显式排除（真消费——宽口径先命中、排除后洗白；删任一条 ⇒ 对应形态变红，见反证钉⑤）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        "op.drop_constraint('ck_x', 't', type_='check')",  # 0116/0062 upgrade 段实例
        'op.execute("ALTER TABLE t DROP CONSTRAINT ck_x")',
        'op.execute("ALTER TABLE t ALTER COLUMN c SET DEFAULT 0")',  # 0095 实例
    ],
)
def test_explicit_exclusions_whitelist(body: str) -> None:
    assert scan_unsafe_lines(_mig(upgrade=body)) == [], body


def test_exclusion_is_consumed_not_decorative() -> None:
    """排除项必须「先有宽命中、后被洗白」——若宽口径根本不匹配 drop_constraint，
    排除项即装饰。验证：把 EXPLICIT_EXCLUSIONS 清空后 drop_constraint 会命中。"""
    body = "op.drop_constraint('ck_x', 't', type_='check')"
    assert not scan_unsafe_lines(_mig(upgrade=body))
    saved = EXPLICIT_EXCLUSIONS
    try:
        import src.data_platform.migration_policy as mp

        mp.EXPLICIT_EXCLUSIONS = tuple()
        assert scan_unsafe_lines(_mig(upgrade=body)), "排除项清空后必须变红（排除被真消费）"
    finally:
        import src.data_platform.migration_policy as mp

        mp.EXPLICIT_EXCLUSIONS = saved


# ---------------------------------------------------------------------------
# 声明解析 / 段截断
# ---------------------------------------------------------------------------

def test_parse_declaration_forms() -> None:
    d = parse_declaration('# EXPAND-CONTRACT: phase=expand pair=0122\n"""x"""\n')
    assert d == {"phase": "expand", "pair": "0122", "legacy": False, "reason": None}
    d = parse_declaration('# EXPAND-CONTRACT: legacy reason="早期实例"\n"""x"""\n')
    assert d["legacy"] is True and d["reason"] == "早期实例" and d["phase"] is None
    d = parse_declaration('"""无声明"""\n')
    assert d["phase"] is None and d["legacy"] is False


def test_scan_upgrade_section_truncates() -> None:
    """downgrade 段内的破坏性 op 是合法回滚路径——不得命中。"""
    text = (
        '"""x"""\ndef upgrade() -> None:\n    op.drop_table("t")\n\n\n'
        'def downgrade() -> None:\n    op.drop_table("t")\n'
    )
    seg = scan_upgrade_section(text)
    assert "def downgrade" not in seg
    hits = scan_unsafe_lines(text)
    assert len(hits) == 1 and "upgrade" not in hits[0][3]


def test_missing_downgrade_reads_to_eof() -> None:
    text = '"""x"""\ndef upgrade() -> None:\n    op.drop_table("t")\n'
    assert scan_unsafe_lines(text), "缺 def downgrade 时截到文件尾（仍命中）"


def test_no_upgrade_no_hit() -> None:
    assert scan_unsafe_lines('"""x"""\nrevision="0099"\n') == []


# ---------------------------------------------------------------------------
# 受管集（内容指纹差集，非文件名差集——复审 P1-6）
# ---------------------------------------------------------------------------

def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def test_managed_set_content_hash(tmp_path: Path) -> None:
    new, prev = tmp_path / "new", tmp_path / "prev"
    new.mkdir()
    prev.mkdir()
    base = _mig()
    _write(prev / "0001_a.py", base)
    _write(new / "0001_a.py", base + "\n# 内容变更\n")  # 同名就地改 ⇒ 必须落网
    managed, _ = managed_set(new, prev)
    assert managed == ["0001_a.py"]


def test_managed_set_new_and_unchanged(tmp_path: Path) -> None:
    new, prev = tmp_path / "new", tmp_path / "prev"
    new.mkdir()
    prev.mkdir()
    base = _mig()
    _write(prev / "0001_a.py", base)
    _write(new / "0001_a.py", base)  # 未变 ⇒ 不受管
    _write(new / "0002_b.py", base)  # 新增 ⇒ 受管
    managed, _ = managed_set(new, prev)
    assert managed == ["0002_b.py"]


def test_managed_set_undeterminable(tmp_path: Path) -> None:
    new = tmp_path / "new"
    new.mkdir()
    _write(new / "0001_a.py", _mig())
    managed, _note = managed_set(new, None)
    assert managed is None  # 首部署 ⇒ 不可判定（部署门 rc=3）
    managed, _note = managed_set(new, tmp_path / "nope")
    assert managed is None  # prev 无效 ⇒ 不可判定


# ---------------------------------------------------------------------------
# evaluate 契约（含 pair 锚定已部署链——同发布捆绑拒）
# ---------------------------------------------------------------------------

def _pair_fixture(tmp_path: Path) -> tuple[Path, Path]:
    new, prev = tmp_path / "new", tmp_path / "prev"
    new.mkdir()
    prev.mkdir()
    expand = _mig('# EXPAND-CONTRACT: phase=expand pair=0099')
    contract = _mig(
        '# EXPAND-CONTRACT: phase=contract pair=0098',
        upgrade='op.execute("DROP TABLE old_t")',
    )
    _write(prev / "0098_e.py", expand)
    _write(new / "0098_e.py", expand)
    _write(new / "0099_c.py", contract)
    return new, prev


def test_contract_pair_deployed_ok(tmp_path: Path) -> None:
    new, prev = _pair_fixture(tmp_path)
    ok, why = evaluate(new / "0099_c.py", new, prev)
    assert ok and "已上产" in why


def test_contract_bundled_with_expand_rejected(tmp_path: Path) -> None:
    """同发布捆绑（expand 未上产）⇒ 拒——两步走的定义就是跨发布（产出 6 边界②）。"""
    new, prev = _pair_fixture(tmp_path)
    (prev / "0098_e.py").unlink()  # expand 只存在于本版 ⇒ 未上产
    ok, why = evaluate(new / "0099_c.py", new, prev)
    assert not ok and "跨发布" in why


def test_expand_with_hit_rejected(tmp_path: Path) -> None:
    new, _ = _pair_fixture(tmp_path)
    bad = _mig('# EXPAND-CONTRACT: phase=expand pair=0099',
               upgrade='op.execute("DROP TABLE old_t")')
    _write(new / "0097_bad.py", bad)
    ok, why = evaluate(new / "0097_bad.py", new, new)
    assert not ok and "expand" in why


def test_undeclared_hit_rejected(tmp_path: Path) -> None:
    new, prev = _pair_fixture(tmp_path)
    bad = _mig(upgrade='op.execute("DROP TABLE old_t")')
    _write(new / "0096_bad.py", bad)
    ok, why = evaluate(new / "0096_bad.py", new, prev)
    assert not ok and "未声明" in why


def test_legacy_hit_rejected_singly(tmp_path: Path) -> None:
    new, prev = _pair_fixture(tmp_path)
    bad = _mig('# EXPAND-CONTRACT: legacy reason="历史"', upgrade='op.execute("DROP TABLE old_t")')
    _write(new / "0095_bad.py", bad)
    ok, why = evaluate(new / "0095_bad.py", new, prev)
    assert not ok and "legacy" in why


# ---------------------------------------------------------------------------
# check_release / CLI 退出码
# ---------------------------------------------------------------------------

def test_check_release_rc_matrix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    new, prev = _pair_fixture(tmp_path)
    rc, lines = check_release(new, prev)
    assert rc == 0 and capsys.readouterr()  # 首行=受管统计

    rc, lines = check_release(tmp_path / "empty_none", None)
    assert rc == 2  # 新目录不存在 ⇒ 内部错

    (tmp_path / "empty_new").mkdir()
    rc, _lines = check_release(tmp_path / "empty_new", None)
    assert rc == 3  # 无上一版 ⇒ 不可判定


def test_check_release_reject_has_interface_line(tmp_path: Path) -> None:
    """rc=1 时 stdout 必含 `✗ 破坏性 DDL 命中: <file>`——S2/S7 沙箱判据接口。"""
    new, prev = _pair_fixture(tmp_path)
    _write(new / "0095_bad.py", _mig(upgrade='op.execute("DROP TABLE old_t")'))
    rc, lines = check_release(new, prev)
    assert rc == 1
    joined = "\n".join(lines)
    assert re.search(r"✗ 破坏性 DDL 命中: 0095_bad\.py", joined)


def _run_cli(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(_MODULE), *args], capture_output=True, check=False
    )


def test_cli_exit_codes(tmp_path: Path) -> None:
    new, prev = _pair_fixture(tmp_path)
    assert _run_cli("--check-release", str(new), str(prev)).returncode == 0
    _write(new / "0095_bad.py", _mig(upgrade='op.execute("DROP TABLE old_t")'))
    r = _run_cli("--check-release", str(new), str(prev))
    assert r.returncode == 1 and "破坏性 DDL 命中" in r.stdout.decode()
    assert _run_cli("--check-release", str(tmp_path / "nope")).returncode == 2
    assert _run_cli().returncode == 2  # 缺子命令 ⇒ 用法错（argparse 天然 2）
    assert _run_cli("--check-release", str(tmp_path)).returncode == 3  # 空目录 + 无 prev


# ---------------------------------------------------------------------------
# 清单反向校验（照 test_exempt_entries_are_live_and_reasoned 先例）
# ---------------------------------------------------------------------------

_CORPUS = [
    "DROP TABLE t",
    "op.drop_table('t')",
    "op.drop_column('t', 'c')",
    "op.drop_index('ix')",
    "ALTER TABLE t ALTER COLUMN c TYPE varchar(8)",
    "op.alter_column('t', 'c', type_=None)",
    "op.alter_column('t', 'c', new_column_name='x')",
    "ALTER TABLE t RENAME COLUMN a TO b",
    "ALTER TABLE t RENAME TO x",
    "op.rename_table('a', 'b')",
]


@pytest.mark.parametrize("idx", range(len(list(ROLLBACK_UNSAFE_PATTERNS))))
def test_unsafe_patterns_live_and_reasoned(idx: int) -> None:
    rx, kind, why = ROLLBACK_UNSAFE_PATTERNS[idx]
    assert any(re.search(rx, c) for c in _CORPUS), \
        f"判定表第 {idx} 条在语料上零命中（陈旧清单）: {rx}"
    assert kind and len(why) >= 8, f"第 {idx} 条缺类别或「为何危险」: {rx}"


@pytest.mark.parametrize("idx", range(len(EXPLICIT_EXCLUSIONS)))
def test_exclusion_patterns_live_and_reasoned(idx: int) -> None:
    rx, why = EXPLICIT_EXCLUSIONS[idx]
    corpus = [
        "ALTER TABLE t DROP CONSTRAINT ck",
        "op.drop_constraint('ck', 't', type_='check')",
        "ALTER TABLE t ALTER COLUMN c SET DEFAULT 0",
    ]
    assert any(re.search(rx, c) for c in corpus), f"排除表第 {idx} 条零命中（陈旧）: {rx}"
    assert len(why) >= 8, f"排除表第 {idx} 条缺理由: {rx}"


# ---------------------------------------------------------------------------
# 步 4 双盲审修复项（P0-1 / P0-2 / P1-a / legacy-phase 互斥）
# ---------------------------------------------------------------------------

def test_legacy_frozen_nonempty() -> None:
    """P0-1：冻结 legacy 白名单是「门验证 legacy 标记」的唯一真源，不得为空。"""
    from src.data_platform.migration_policy import LEGACY_FROZEN

    assert LEGACY_FROZEN, "冻结 legacy 白名单为空＝假绿"


def test_nonfrozen_legacy_with_hit_rejected_at_gate(tmp_path: Path) -> None:
    """P0-1（门）：新迁移标 legacy + 破坏性 op ⇒ 部署门**拒**（旧版零验证放行 rc=0）。"""
    new, prev = _pair_fixture(tmp_path)
    _write(new / "0199_evil.py",
           _mig('# EXPAND-CONTRACT: legacy reason="自称历史"',
                upgrade='op.execute("DROP TABLE important")'))
    rc, lines = check_release(new, prev)
    joined = "\n".join(lines)
    assert rc == 1, f"非冻结 legacy 必须拒（实得 {rc}）:\n{joined}"
    assert "非冻结 legacy" in joined


def test_frozen_legacy_body_unchanged_not_blocking(tmp_path: Path) -> None:
    """冻结集内 legacy、upgrade 段与已部署版一致 ⇒ 可见不阻断（本批 15 文件加声明行自洽）。"""
    new, prev = _pair_fixture(tmp_path)
    name = "0104_rename_venue_to_account.py"
    body = ('"""x"""\nrevision = "0104"\ndef upgrade() -> None:\n'
            '    op.execute("ALTER TABLE t RENAME COLUMN a TO b")\n\ndef downgrade() -> None:\n    pass\n')
    _write(prev / name, body)
    _write(new / name, '# EXPAND-CONTRACT: legacy reason="事故当事人"\n' + body)
    rc, lines = check_release(new, prev)
    joined = "\n".join(lines)
    assert rc == 0, joined
    assert "不阻断" in joined and name in joined


def test_frozen_legacy_body_tampered_rejected(tmp_path: Path) -> None:
    """冻结集内 legacy 的 upgrade 段被改（偷加破坏性 op）⇒ 拒（门验证内容，非只信名字）。"""
    new, prev = _pair_fixture(tmp_path)
    name = "0104_rename_venue_to_account.py"
    body = ('"""x"""\nrevision = "0104"\ndef upgrade() -> None:\n'
            '    op.execute("ALTER TABLE t RENAME COLUMN a TO b")\ndef downgrade() -> None:\n    pass\n')
    _write(prev / name, body)
    _write(new / name, '# EXPAND-CONTRACT: legacy reason="x"\n'
                       + body.replace("RENAME COLUMN a TO b", "DROP COLUMN a"))
    rc, lines = check_release(new, prev)
    assert rc == 1 and "发生变化" in "\n".join(lines)


def test_legacy_phase_mutual_exclusion(tmp_path: Path) -> None:
    """`legacy` 与 `phase`/`pair` 不得同现（防一行声明拿双通道）。"""
    new, prev = _pair_fixture(tmp_path)
    _write(new / "0198_x.py",
           _mig('# EXPAND-CONTRACT: legacy phase=contract pair=0116',
                upgrade='op.execute("DROP TABLE t")'))
    ok, why = evaluate(new / "0198_x.py", new, prev)
    assert not ok and "互斥" in why
    rc, lines = check_release(new, prev)
    assert rc == 1 and "互斥" in "\n".join(lines)


def test_indecodable_file_gives_rc2_not_1(tmp_path: Path) -> None:
    """P0-2：模块内部错（解码失败，属 ValueError 非 OSError）恒返 **2**——绝不伪装成 1。

    否则 `failed_when` 的封闭式补集会把 rc=1 当「命中」，`allow_contract=true` 时被豁免 ⇒
    门崩溃被静默放行。
    """
    new, prev = tmp_path / "new", tmp_path / "prev"
    new.mkdir()
    prev.mkdir()
    good = b'"""x"""\nrevision="1"\ndef upgrade() -> None:\n    pass\ndef downgrade() -> None:\n    pass\n'
    (prev / "0001_a.py").write_bytes(good)
    (new / "0001_a.py").write_bytes(good + b"\xff\xfe")  # 非法 UTF-8：read_bytes 过、read_text 崩
    rc, lines = check_release(new, prev)
    assert rc == 2, f"期望 2（内部错），实得 {rc}: {lines}"
    assert "内部错误" in "\n".join(lines)


# ---------------------------------------------------------------------------
# 步 4 复审轮② 修复项（P0-A 冻结集 keying 与声明解耦 / P0-B 大小写不敏感）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        'op.execute("drop table demo_t")',                     # P0-B 复现原式
        'op.execute("alter table t rename to t2")',
        'op.execute("alter table t alter column c type varchar(8)")',
        'op.execute("Drop Table demo_t")',                     # 混合大小写
        "op.DROP_TABLE('t')",                                  # pythonic 形态大写
    ],
)
def test_lowercase_sql_hits_like_uppercase(body: str) -> None:
    """P0-B（门）：判定表须 `re.IGNORECASE`——SQL 关键字大小写不敏感，小写拼写不得静默放行。"""
    assert scan_unsafe_lines(_mig(upgrade=body)), f"小写/混合大小写须与全大写同判: {body}"


def test_lowercase_exclusion_still_consumed() -> None:
    """排除项同样须 re.I——否则「宽口径先命中、排除后洗白」在小写拼写下断裂（放行而非洗白）。"""
    body = 'op.execute("alter table t drop constraint ck_x")'
    assert scan_unsafe_lines(_mig(upgrade=body)) == [], "小写 DROP CONSTRAINT 仍须豁免"
    import src.data_platform.migration_policy as mp

    saved = mp.EXPLICIT_EXCLUSIONS
    try:
        mp.EXPLICIT_EXCLUSIONS = tuple()
        assert scan_unsafe_lines(_mig(upgrade=body)), \
            "裸 SQL 形态的排除项须**真消费**（删排除项 ⇒ 该语句变红）"
    finally:
        mp.EXPLICIT_EXCLUSIONS = saved


def _frozen_fixture(tmp_path: Path, new_decl: str, tamper: bool) -> tuple[Path, Path]:
    """冻结集内文件已上产（prev 有同名同体），new 侧按参数声明 + 可选篡改。"""
    from src.data_platform.migration_policy import LEGACY_FROZEN

    new, prev = tmp_path / "new", tmp_path / "prev"
    new.mkdir()
    prev.mkdir()
    name = sorted(LEGACY_FROZEN)[0]
    base = ('"""x"""\nrevision = "0100"\ndef upgrade() -> None:\n'
            '    op.execute("SELECT 1")\ndef downgrade() -> None:\n    pass\n')
    body = base.replace("SELECT 1", "DROP TABLE important_t") if tamper else base
    _write(prev / name, '# EXPAND-CONTRACT: legacy reason="历史"\n' + base)
    expand = _mig('# EXPAND-CONTRACT: phase=expand pair=0099')
    _write(prev / "0098_e.py", expand)
    _write(new / "0098_e.py", expand)
    _write(new / name, (f'{new_decl}\n' if new_decl else "") + body)
    return new, prev


@pytest.mark.parametrize(
    "body",
    [
        # P0-C：排除项关键词出现在**同一语句的其它位置**——不得整句赦免。
        'op.execute("DROP TABLE old_t -- drop constraint later")',     # SQL 行内注释（本轮回归式）
        'op.execute("DROP TABLE old_t /* drop constraint */")',        # 块注释
        'op.execute("ALTER TABLE t DROP CONSTRAINT ck_x, DROP COLUMN c")',       # 逗号并列 action
        'op.execute("ALTER TABLE t ALTER COLUMN c SET DEFAULT 1, DROP COLUMN d")',
        'op.execute("ALTER TABLE t DROP CONSTRAINT ck_x, RENAME TO t2")',
        # P0-D：排除项 #3 曾贪婪跨分隔符 ⇒ 吃掉同语句另一个 §1.1 明列 op
        'op.execute("ALTER TABLE t ALTER COLUMN c TYPE int, ALTER COLUMN d SET DEFAULT 1")',
        'op.execute("ALTER COLUMN c, set default 1, TYPE varchar(8)")',
        'op.execute("ALTER COLUMN c DROP COLUMN c set default 1")',              # 空格分隔（无逗号）
        'op.execute("ALTER COLUMN c RENAME TO t2 set default 1 -- drop constraint later")',
    ],
)
def test_exclusion_does_not_whitelist_whole_statement(body: str) -> None:
    """P0-C（门）：规范 §1.3 豁免的是**形态**（`DROP CONSTRAINT` / `SET DEFAULT`），
    不是**整语句**——关键词出现在注释或并列 action 中时，同语句的破坏性 op 仍须命中。

    ⚠ 其中前两例是**本轮回归**：`re.IGNORECASE` 之前 `drop constraint`（小写）不匹配排除项
    ⇒ 旧实现反而命中；加 re.I 后整句被豁免 ⇒ 由红变绿（门比修复前更弱）。
    """
    assert scan_unsafe_lines(_mig(upgrade=body)), f"排除项不得整句赦免: {body}"


def test_exclusion_subtractive_keeps_true_exclusions(tmp_path: Path) -> None:
    """减法与收窄不得误伤真排除形态（0116/0062/0095）——收窄只为闭合漏检，不是改判放宽类。"""
    for body in (
        'op.execute("ALTER TABLE t DROP CONSTRAINT ck_x")',
        "op.drop_constraint('ck_x', 't', type_='check')",
        'op.execute("ALTER TABLE t ALTER COLUMN c SET DEFAULT 0")',
        'op.execute("ALTER TABLE t ALTER COLUMN c set default 0")',        # 小写变体
        'op.execute("ALTER TABLE tbl ALTER COLUMN dataset_version SET DEFAULT 2")',  # 0095 实际形态
    ):
        assert scan_unsafe_lines(_mig(upgrade=body)) == [], body
    _ = tmp_path


def test_frozen_legacy_relabeled_contract_still_body_checked(tmp_path: Path) -> None:
    """P0-A（门）：冻结集 keying＝**文件名**，与声明解耦——重标 `phase=contract` 不得旁路
    「upgrade 段逐字一致」比对（旧实现读 `decl["legacy"]` ⇒ 实测 rc=0 放行）。"""
    new, prev = _frozen_fixture(
        tmp_path, '# EXPAND-CONTRACT: phase=contract pair=0098', tamper=True)
    rc, lines = check_release(new, prev)
    joined = "\n".join(lines)
    assert rc == 1, f"冻结集内文件 body 被改 ⇒ 必须拒（实得 {rc}）:\n{joined}"
    assert "发生变化" in joined


def test_frozen_legacy_relabeled_unchanged_not_blocking(tmp_path: Path) -> None:
    """同声明、body 未变 ⇒ 可见不阻断（冻结集的名义是「历史遗留」，不是「一律拒」）。"""
    new, prev = _frozen_fixture(
        tmp_path, '# EXPAND-CONTRACT: phase=contract pair=0098', tamper=False)
    rc, lines = check_release(new, prev)
    joined = "\n".join(lines)
    assert rc == 0, joined
    assert "不阻断" in joined


def test_exclusion_narrowed_comment_and_semicolon() -> None:
    """P1-a：排除项**不再整行赦免**——注释/分号同行的破坏性 op 必须命中。"""
    assert scan_unsafe_lines(_mig(upgrade='op.drop_table("t")  # 与 DROP CONSTRAINT 无关'))
    assert scan_unsafe_lines(_mig(upgrade='op.drop_table("t"); op.drop_constraint("ck","t")'))
    assert scan_unsafe_lines(_mig(
        upgrade='op.execute("ALTER TABLE t DROP CONSTRAINT ck; DROP COLUMN c")'))
    # 而真排除项仍豁免（收窄不得误伤 0116/0062/0095 形态）
    assert not scan_unsafe_lines(_mig(upgrade="op.drop_constraint('ck','t',type_='check')"))
    assert not scan_unsafe_lines(_mig(upgrade='op.execute("ALTER TABLE t ALTER COLUMN c SET DEFAULT 0")'))
