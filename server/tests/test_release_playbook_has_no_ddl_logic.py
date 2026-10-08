"""release.yml 阶段 4「bash 零判定」的机械执行者（批 111 产出 5⑧ / 反证钉④ / P0-β）。

P0-β 的教训：反面枚举清单（「不得出现 grep/awk/RENAME…」）必漂——补清单治不了病。
本闸门只用**封闭正向判据（allow-list）**：

⑴ 判定入口唯一性（结构必然性，非语汇黑名单）：**任何 DDL 判定必读迁移目录** ⇒
  全 playbook 内引用 `migrations/versions` 的任务恰 1 个、`migration_policy.py` 字面恰 1 次
  ⇒ 把判定挪到 pre_tasks/post_tasks/另一个 block ⇒ 引用任务数变 2 ⇒ 红。
⑵ 阶段 4 段内**命令 token ⊆ {set, readlink, <deploy_root>/shared/venv/bin/python}**（allow-list）。
⑶ 无分支控制关键字（`||`/`&&`/`${x:+y}` 属取值兜底，不在禁列）。
⑷ python 调用形态白名单：首个参数须是 `"$rel/src/data_platform/migration_policy.py"`——
  **`python -c` 明令禁**（内联判定可含白名单 token 而逃逸）。
⑸ `failed_when` 归一化全文 == 期望常量（不是「含三字面」——后者可被 `rc == 1 and False` 骗过）。
⑹ 分词器自测（守卫自身的守卫）+ ⑺ 注入反例（副本上断言器能红）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PLAYBOOK = Path(__file__).resolve().parents[2] / "deploy" / "playbooks" / "release.yml"
_STAGE4_NAME = "阶段 4 —— 本版新增迁移的破坏性 DDL 门"
_ALLOWED_TOKENS = {"set", "readlink", "{{ deploy_root }}/shared/venv/bin/python"}
_FAILED_WHEN_EXPECTED = re.sub(
    r"\s+", " ",
    "not (ddl_gate.rc == 0 or ddl_gate.rc == 3"
    " or (ddl_gate.rc == 1 and (allow_contract | default(false) | bool)))",
).strip()
_BRANCH_KEYWORDS = {"if", "then", "else", "elif", "fi", "case", "esac", "while", "for", "until"}


def _playbook_text() -> str:
    text = _PLAYBOOK.read_text(encoding="utf-8")
    assert text, "release.yml 为空＝假绿"
    return text


def _stage4_script(text: str) -> str:
    """抽阶段 4 的 shell 脚本体：`shell: |` 之后到 `register:` 行之前。"""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if _STAGE4_NAME in ln)
    sh = next(i for i in range(start, len(lines)) if "ansible.builtin.shell: |" in lines[i])
    end = next(i for i in range(sh, len(lines)) if re.match(r"\s+register: ddl_gate", lines[i]))
    return "\n".join(lines[sh + 1 : end])


def _first_word(segment: str) -> str | None:
    """一段命令的首词；`${…}` 开头 ⇒ 无命令；赋值行 ⇒ 取 $(…) 内首词；
    引号开头 ⇒ 引号内整体是一个词（模板路径含空格，split 会切碎）。"""
    seg = segment.strip()
    if not seg or seg.startswith("#"):
        return None
    if seg.startswith("${"):
        return None
    m = re.match(r"^\w+=(.*)$", seg, re.S)
    if m:
        inner = m.group(1)
        cm = re.match(r"^\$\((.*)\)\s*$", inner.strip(), re.S)
        if cm:
            return _first_word(cm.group(1))
        return None  # 纯赋值（无命令替换）＝无命令
    if seg[0] in "\"'":
        end = seg.find(seg[0], 1)
        if end != -1:
            return seg[1:end]
    word = seg.split()[0].strip("\"'")
    if word.endswith("\\"):
        word = word[:-1]
    return word


def _command_tokens(script: str) -> set[str]:
    """分词：续行拼接 → 赋值行 `x=$(…)` 整体保护（内部 || 不切段）→ 其余按 ;/|/&/换行
    切段取首词。**不用 shlex**——它在 $(…)/${…:+…}/'{{ }}' 上失真（设计稿钉④ 分词器纪律）。"""
    joined = re.sub(r"\\\s*\n\s*", " ", script)
    tokens: set[str] = set()
    for line in joined.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\w+=\$\((.*)\)\s*$", line, re.S)
        if m:
            w = _first_word(m.group(1))
            if w:
                tokens.add(w)
            continue
        for seg in re.split(r"[;|&]", line):
            w = _first_word(seg)
            if w:
                tokens.add(w)
    return tokens


# ---------------------------------------------------------------------------
# ⑹ 分词器自测
# ---------------------------------------------------------------------------

def test_tokenizer_self_test() -> None:
    assert _command_tokens("set -u\nprev=$(readlink -f '/x' 2>/dev/null || true)") == {
        "set", "readlink",
    }
    assert _command_tokens("${prev:+\"$prev/x\"}") == set()  # ${…:+…} 不是命令
    assert _command_tokens("'{{ deploy_root }}/shared/venv/bin/python' \"$f\" \\ \n  --check") == {
        "{{ deploy_root }}/shared/venv/bin/python",
    }
    assert _command_tokens("rel='{{ deploy_root }}/releases/x'") == set()  # 纯赋值
    assert _command_tokens("grep -q x | sort") == {"grep", "sort"}  # 管道分段


# ---------------------------------------------------------------------------
# ⑴ 判定入口唯一性（结构必然性）
# ---------------------------------------------------------------------------

def _tasks_referencing(text: str, needle: str) -> list[str]:
    """粗粒度任务切分：按 `- name:` 行分块，返回包含 needle 的任务名列表。"""
    tasks: list[str] = []
    current: list[str] | None = None
    for ln in text.splitlines():
        if re.match(r"\s*-\s+name:", ln):
            if current is not None and any(needle in l for l in current):
                tasks.append(current[0])
            current = [ln]
        elif current is not None:
            current.append(ln)
    if current is not None and any(needle in l for l in current):
        tasks.append(current[0])
    return tasks


def test_ddl_judgment_entry_is_unique() -> None:
    text = _playbook_text()
    ref_tasks = _tasks_referencing(text, "migrations/versions")
    assert len(ref_tasks) == 1, (
        f"引用 migrations/versions 的任务必须恰 1 个（判定入口唯一），实得 {len(ref_tasks)}: "
        f"{[t.strip() for t in ref_tasks]}——新增判定必读迁移目录 ⇒ 任何旁路判定都会让计数变 2"
    )
    code_lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    mp_tasks = _tasks_referencing("\n".join(code_lines), "migration_policy.py")
    assert len(mp_tasks) == 1, (
        f"非注释行引用 migration_policy.py 的任务须恰 1 个（判定真源唯一调用点），"
        f"实得 {len(mp_tasks)}: {[t.strip()[:60] for t in mp_tasks]}"
    )


# ---------------------------------------------------------------------------
# ⑵⑶⑷ 阶段 4 段：命令白名单 + 无分支 + python 形态
# ---------------------------------------------------------------------------

def test_stage4_command_allowlist() -> None:
    script = _stage4_script(_playbook_text())
    tokens = _command_tokens(script)
    unexpected = tokens - _ALLOWED_TOKENS
    assert not unexpected, (
        f"阶段 4 出现白名单外命令 token: {sorted(unexpected)}——bash 零判定被破坏"
        f"（allow-list 判据，allow={sorted(_ALLOWED_TOKENS)}）"
    )


def test_stage4_no_branch_control() -> None:
    script = _stage4_script(_playbook_text())
    joined = re.sub(r"\\\s*\n\s*", " ", script)
    for seg in re.split(r"[;|&\n]", joined):
        w = _first_word(seg)
        assert w not in _BRANCH_KEYWORDS, f"阶段 4 出现分支控制关键字: {w}（段内不得有判定）"


def test_stage4_python_form_whitelist() -> None:
    """python 调用首个参数必须是模块路径——禁 python -c（内联判定逃逸通道）。"""
    script = _stage4_script(_playbook_text())
    joined = re.sub(r"\\\s*\n\s*", " ", script)
    for seg in re.split(r"[;|&\n]", joined):
        seg = seg.strip()
        if not seg.startswith("'{{ deploy_root }}/shared/venv/bin/python'"):
            continue
        m = re.match(r"'[^']*'\s+(\"[^\"]*\"|\S+)", seg)
        assert m, f"python 调用缺首个参数: {seg[:80]}"
        assert m.group(1) == '"$rel/src/data_platform/migration_policy.py"', (
            f"python 首参须为模块路径，实得 {m.group(1)}——禁 -c/内联判定"
        )


# ---------------------------------------------------------------------------
# ⑸ failed_when 归一化全文比对
# ---------------------------------------------------------------------------

def test_failed_when_normalized_exact() -> None:
    text = _playbook_text()
    m = re.search(r"failed_when: >\s*\n((?:\s{12}.*\n)+)", text)
    assert m, "找不到阶段 4 的 failed_when 块"
    actual = re.sub(r"\s+", " ", m.group(1)).strip()
    assert actual == _FAILED_WHEN_EXPECTED, (
        f"failed_when 必须逐字等于封闭式补集常量（防局部改写如 rc == 1 and False）：\n"
        f"  期望: {_FAILED_WHEN_EXPECTED}\n  实得: {actual}"
    )


# ---------------------------------------------------------------------------
# ⑺ 注入反例（守卫自身的守卫——用副本，不改真实文件）
# ---------------------------------------------------------------------------

def _assert_stage4_tokens(text: str) -> None:
    script = _stage4_script(text)
    tokens = _command_tokens(script)
    unexpected = tokens - _ALLOWED_TOKENS
    if unexpected:
        pytest.fail(f"白名单外命令: {sorted(unexpected)}")


def test_injection_grep_turns_red() -> None:
    # ⚠ 在 script 副本上注入——对全文 replace 会打到文件头第一个 set -u（不在阶段 4）。
    script = _stage4_script(_playbook_text())
    assert not (_command_tokens(script) - _ALLOWED_TOKENS)  # 真文件绿
    poisoned = script.replace("set -u", "set -u\ngrep -q 'DROP TABLE' x || true", 1)
    bad = _command_tokens(poisoned) - _ALLOWED_TOKENS
    assert "grep" in bad, f"注入 grep ⇒ 必须被检出，实得 {bad}"


def test_injection_branch_turns_red() -> None:
    script = _stage4_script(_playbook_text())
    poisoned = script.replace("set -u", "set -u\nif [ -n x ]; then echo y; fi", 1)
    words = {_first_word(s) for s in re.split(r"[;|&\n]", poisoned)}
    assert words & _BRANCH_KEYWORDS, f"注入 if ⇒ 必须被检出，实得 {words}"


def test_injection_post_tasks_turns_red() -> None:
    text = _playbook_text()
    assert len(_tasks_referencing(text, "migrations/versions")) == 1
    poisoned = text + (
        "\n  post_tasks_extra:\n    - name: 旁路判定任务\n"
        "      ansible.builtin.shell: ls 'x/migrations/versions'\n"
    )
    assert len(_tasks_referencing(poisoned, "migrations/versions")) == 2, "判定挪 post_tasks ⇒ 计数变 2 ⇒ 红"


def test_injection_failed_when_rewrite_turns_red() -> None:
    text = _playbook_text()
    poisoned = text.replace("ddl_gate.rc == 1 and (allow_contract | default(false) | bool)",
                            "ddl_gate.rc != 0 and not allow_contract")
    m = re.search(r"failed_when: >\s*\n((?:\s{12}.*\n)+)", poisoned)
    actual = re.sub(r"\s+", " ", m.group(1)).strip()
    assert actual != _FAILED_WHEN_EXPECTED, "failed_when 改回开放式合取 ⇒ 归一化比对必须变红"
