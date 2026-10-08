"""release.yml 阶段 4「bash 零判定」的机械执行者（批 111 产出 5⑧ / 反证钉④ / P0-β）。

P0-β 的教训：反面枚举清单（「不得出现 grep/awk/RENAME…」）必漂——补清单治不了病。
本闸门只用**封闭正向判据（allow-list）**：

⑴ 判定入口唯一性（结构必然性，非语汇黑名单）：**任何 DDL 判定必读迁移目录** ⇒
  **扫描语料＝release.yml ＋ 其 include_tasks/import_tasks 目标**（步 4 同判 P1-c）内
  引用 `migrations/versions` 的任务恰 1 个、`migration_policy.py` 字面恰 1 次
  ⇒ 把判定挪到 pre_tasks/post_tasks/另一个 block/**被包含文件** ⇒ 引用任务数变 2 ⇒ 红。
⑵ 阶段 4 段内**命令 token ⊆ {set, readlink, /usr/bin/python3}**（allow-list）。
  **解释器必须是系统 python3，不得是应用 venv**——`shared/` 为 `0700 quant:quant`，
  deploy 身份物理上读不到 `shared/venv`，旧写法在真机 rc=126，配合「门恒跑＋封闭式补集」
  ＝拦死每一次发布（2026-10-08 实测）。理由属**依赖方向**：门自称纯 stdlib 就不该依赖应用 venv。
  ⇒ 单列 `test_stage4_runs_under_system_interpreter_not_app_venv` 承载「为什么」。
⑵b `auto_rollback_disabled` 的 `when` 归一化全文 == 「豁免**生效**」常量（绑事实不绑意图——
  2026-10-08 修：旧判据绑 `allow_contract` 旗标，rc=0 的干净发布也白丢自动回滚）。
⑶ 无分支控制关键字（`||`/`&&`/`${x:+y}` 属取值兜底，不在禁列）。
⑷ python 调用形态白名单：首个参数须是 `"$rel/src/data_platform/migration_policy.py"`——
  **`python -c` 明令禁**（内联判定可含白名单 token 而逃逸）。
⑸ `failed_when` 归一化全文 == 期望常量（不是「含三字面」——后者可被 `rc == 1 and False` 骗过）。
⑹ 分词器自测（守卫自身的守卫）+ ⑺ 注入反例（副本上断言器能红）。
⑻ **全文**解释器守卫：本 playbook 出现的每一个解释器路径都必须是 `/usr/bin/python3`
  （类级收口——⑵c 只管阶段 4 段，而 file 模式支一度有同形 `shared/venv/bin/python`）。
  只扫**非注释行**：注释/文案引用历史形态属说明面，不是执行面。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PLAYBOOK = Path(__file__).resolve().parents[2] / "deploy" / "playbooks" / "release.yml"
_STAGE4_NAME = "阶段 4 —— 本版新增迁移的破坏性 DDL 门"
_ALLOWED_TOKENS = {"set", "readlink", "/usr/bin/python3"}
_STAGE4_INTERPRETER = "/usr/bin/python3"
_APP_VENV_FRAGMENT = "shared/venv"
_AUTO_ROLLBACK_WHEN_EXPECTED = "ddl_gate.rc == 1 and (allow_contract | default(false) | bool)"
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
    assert _command_tokens("'/usr/bin/python3' \"$f\" \\ \n  --check") == {"/usr/bin/python3"}
    assert _command_tokens("'/opt/has space/py3' x") == {"/opt/has space/py3"}  # 引号内空格不切词
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
            if current is not None and any(needle in body_line for body_line in current):
                tasks.append(current[0])
            current = [ln]
        elif current is not None:
            current.append(ln)
    if current is not None and any(needle in body_line for body_line in current):
        tasks.append(current[0])
    return tasks


_PLAYBOOK_DIR = _PLAYBOOK.parent


def _include_targets(text: str) -> list[Path]:
    """收集 `include_tasks:`/`import_tasks:` 的静态目标（相对 playbook 目录）。

    ⚠ 步 4 同判 P1-c：判定若逃逸到被包含文件，只扫 release.yml 会漏 ⇒ 扫描语料须含之。
    **模板化目标**（含 `{{`）无法静态解析 ⇒ 直接断言其不存在（防静默漏扫）。
    """
    targets: list[Path] = []
    for m in re.finditer(r"^\s*(?:include_tasks|import_tasks):\s*(.+?)\s*$", text, re.M):
        raw = m.group(1).strip().strip("\"'")
        assert "{{" not in raw, f"include 目标被模板化，守卫无法覆盖（须改静态路径）: {raw}"
        targets.append(_PLAYBOOK_DIR / raw)
    return targets


def _corpus() -> str:
    """扫描语料＝release.yml ＋ 其 include/import 目标（守卫覆盖被包含文件）。"""
    text = _playbook_text()
    parts = [text]
    for path in _include_targets(text):
        assert path.is_file(), f"include_tasks 目标不存在（守卫无法覆盖）: {path}"
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_ddl_judgment_entry_is_unique() -> None:
    text = _corpus()
    ref_tasks = _tasks_referencing(text, "migrations/versions")
    assert len(ref_tasks) == 1, (
        f"引用 migrations/versions 的任务必须恰 1 个（判定入口唯一；**含 include 目标**），"
        f"实得 {len(ref_tasks)}: {[t.strip() for t in ref_tasks]}——"
        f"新增判定必读迁移目录 ⇒ 任何旁路判定都会让计数变 2"
    )
    code_lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    mp_tasks = _tasks_referencing("\n".join(code_lines), "migration_policy.py")
    assert len(mp_tasks) == 1, (
        f"非注释行引用 migration_policy.py 的任务须恰 1 个（判定真源唯一调用点；含 include 目标），"
        f"实得 {len(mp_tasks)}: {[t.strip()[:60] for t in mp_tasks]}"
    )


def test_include_targets_carry_no_ddl_logic() -> None:
    """步 4 同判 P1-c：被 include 的文件不得含判定入口。"""
    targets = _include_targets(_playbook_text())
    assert targets, "release.yml 已无 include_tasks —— 若确为删光请同步本断言"
    for path in targets:
        body = path.read_text(encoding="utf-8")
        assert "migrations/versions" not in body, f"{path.name}: 含迁移目录引用（判定入口逃逸）"
        assert "migration_policy.py" not in body, f"{path.name}: 含判定模块引用（判定入口逃逸）"


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
        if not seg.startswith(f"'{_STAGE4_INTERPRETER}'"):
            continue
        m = re.match(r"'[^']*'\s+(\"[^\"]*\"|\S+)", seg)
        assert m, f"python 调用缺首个参数: {seg[:80]}"
        assert m.group(1) == '"$rel/src/data_platform/migration_policy.py"', (
            f"python 首参须为模块路径，实得 {m.group(1)}——禁 -c/内联判定"
        )


# ---------------------------------------------------------------------------
# ⑵c 身份落位（解释器）＋ ⑵b auto_rollback 触发判据
# ---------------------------------------------------------------------------

def test_stage4_runs_under_system_interpreter_not_app_venv() -> None:
    """解释器身份断言：阶段 4 必须以**系统 python3** 直跑，不得用应用 venv。

    为什么单列一条：allow-list（⑵）只保证「没有白名单外的 token」，**不表达「为什么必须如此」**。
    而这个「为什么」正是五轮双盲审都没问出来的那一个：
      `shared/` = `0700 quant:quant`（secrets 隔离，`bootstrap.yml:87` 声明）
      ⇒ **deploy 身份物理上进不去** ⇒ 旧写法真机 rc=126 Permission denied；
      而「门恒跑 ＋ 封闭式补集 failed_when」把它升级成**拦死每一次发布**（逃生门只救 rc=1）。
    反证：同一次彩排里阶段 2.5／3 走 `sudo -u quant <wrapper>` **都通过** ⇒ 错的是身份不是环境。
    """
    script = _stage4_script(_playbook_text())
    assert _STAGE4_INTERPRETER in script, (
        f"阶段 4 必须以 {_STAGE4_INTERPRETER} 执行（纯 stdlib 门不得依赖应用 venv）"
    )
    assert _APP_VENV_FRAGMENT not in script, (
        f"阶段 4 不得引用 {_APP_VENV_FRAGMENT}：shared/ 为 0700 quant:quant，deploy 身份读不到"
        f" ⇒ 真机 rc=126，且门恒跑 ⇒ 拦死每次发布（2026-10-08 实测）。需要 quant 身份请走 wrapper。"
    )


def test_injection_app_venv_interpreter_turns_red() -> None:
    """守卫自身的守卫：把解释器改回应用 venv ⇒ 身份断言与 allow-list **都**必须红。"""
    script = _stage4_script(_playbook_text())
    poisoned = script.replace(f"'{_STAGE4_INTERPRETER}'", "'{{ deploy_root }}/shared/venv/bin/python'", 1)
    assert poisoned != script, "注入未生效 ⇒ 本用例是假绿"
    assert _APP_VENV_FRAGMENT in poisoned
    bad = _command_tokens(poisoned) - _ALLOWED_TOKENS
    assert "{{ deploy_root }}/shared/venv/bin/python" in bad, f"allow-list 必须检出，实得 {bad}"


def _auto_rollback_when_index(text: str) -> int:
    """`auto_rollback_disabled: true` 之后第一条 `when:` 的行号。

    ⚠ 不能靠 `str.replace(expr, …, 1)` 定位：同一表达式 `ddl_gate.rc == 1 and (allow_contract …)`
    在**旁路告警/留痕**的 `when` 上也合法出现，`replace` 会打到那两处（实测踩过）。
    """
    lines = text.splitlines()
    i = next((k for k, ln in enumerate(lines) if "auto_rollback_disabled: true" in ln), -1)
    assert i >= 0, "找不到 auto_rollback_disabled 的 set_fact"
    j = next((k for k in range(i, len(lines)) if re.match(r"\s+when:", lines[k])), -1)
    assert j > i, "找不到该 set_fact 的 when"
    return j


def _auto_rollback_when(text: str) -> str:
    line = text.splitlines()[_auto_rollback_when_index(text)]
    return re.sub(r"\s+", " ", line.split("when:", 1)[1]).strip()


def test_auto_rollback_trigger_binds_effective_exemption() -> None:
    """`auto_rollback_disabled` 触发条件逐字 == 「豁免**生效**」常量（绑事实不绑意图）。

    2026-10-08 实测暴露：带 `allow_contract` 而门以 rc=126 挂掉 ⇒ 豁免**从未生效**，
    自动回滚却被无谓关闭（拖累本次发布）。判据：绑「豁免生效」的事实（rc=1 且被放行），
    不绑「请求豁免」的意图（仅旗标）——否则 rc=0 的干净发布也白丢自动回滚。
    """
    actual = _auto_rollback_when(_playbook_text())
    assert actual == _AUTO_ROLLBACK_WHEN_EXPECTED, (
        f"触发条件必须逐字等于「豁免生效」常量：\n  期望: {_AUTO_ROLLBACK_WHEN_EXPECTED}\n  实得: {actual}"
    )


def test_injection_auto_rollback_flag_only_turns_red() -> None:
    """守卫自身的守卫：改回「仅旗标触发」⇒ 归一化比对必须变红（按行定位，不用 replace）。"""
    text = _playbook_text()
    lines = text.splitlines()
    j = _auto_rollback_when_index(text)
    lines[j] = re.sub(r"when:.*", "when: allow_contract | default(false) | bool", lines[j])
    poisoned = "\n".join(lines)
    assert poisoned != text, "注入未生效 ⇒ 本用例是假绿"
    assert _auto_rollback_when(poisoned) != _AUTO_ROLLBACK_WHEN_EXPECTED


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


def test_injection_in_include_target_turns_red() -> None:
    """反向（P1-c）：判定挪进**被 include 的文件** ⇒ 合并语料后引用任务数 = 2 ⇒ 红。"""
    text = _playbook_text()
    assert len(_tasks_referencing(text, "migrations/versions")) == 1
    evil = "    - name: 旁路判定（藏在被包含文件里）\n      ansible.builtin.shell: ls 'x/migrations/versions'\n"
    assert len(_tasks_referencing(text + "\n" + evil, "migrations/versions")) == 2, (
        "判定逃逸到 include 目标 ⇒ 语料合并后必须 > 1"
    )


def test_injection_failed_when_rewrite_turns_red() -> None:
    text = _playbook_text()
    poisoned = text.replace("ddl_gate.rc == 1 and (allow_contract | default(false) | bool)",
                            "ddl_gate.rc != 0 and not allow_contract")
    m = re.search(r"failed_when: >\s*\n((?:\s{12}.*\n)+)", poisoned)
    actual = re.sub(r"\s+", " ", m.group(1)).strip()
    assert actual != _FAILED_WHEN_EXPECTED, "failed_when 改回开放式合取 ⇒ 归一化比对必须变红"


# ---------------------------------------------------------------------------
# ⑻ 全文解释器守卫（补审 A-P1-1 的**类级**收口）
#   阶段 4 的 ⑵c 只覆盖阶段 4 段——可同形的还有「阶段 8 file 模式支」这类**别的**验证步骤。
#   类级判据（依赖方向）：**凡在本 playbook 里出现解释器路径，必须是系统 `/usr/bin/python3`**
#   ——`shared/` 为 `0700 quant:quant`（bootstrap.yml 声明），deploy 身份物理上读不到
#   `shared/venv/bin/python` ⇒ 真机 EACCES/rc=126，配合「门/断言恒跑」＝拦死发布。
#   实现只扫**非注释行**：注释与文案里引用历史形态是**说明**，不得让文档一改就假红。
#   ⚠ 局限（诚实声明③条，均为「本守卫**不覆盖**」而非「已覆盖」）：
#     (i) **inventory 侧**（`deploy/inventory/group_vars/*.yml`）里 `quant_*_cmd` 等变量**指向的**
#         wrapper 脚本内部的解释器——不归本守卫管，属装位侧（人）。
#     (ii) wrapper 脚本自身（`deploy/wrappers/*`）内部怎么写解释器——同上。
#     (iii) ansible `--tags/--skip-tags` 可让阶段 4 整体 skipped（`ddl_gate` 未注册 ⇒ 下游
#         `when: ddl_gate.rc == 1` 求值 false ⇒ **门不红、发布静默过**）。本 playbook 的唯一
#         入口 `run_release` 不带 tags，故不可达；已挂 `flow/待办.md` 🔖（须配机械执行者）。
#   ⚠ 判据同时覆盖**裸 `python`/`python3`**（依赖 PATH 解析——若某任务把应用 venv 前置进
#   PATH 即同族 rc=126），故「解释器一律写绝对路径 `/usr/bin/python3`」是全须遵守的形态。
#   ⚠ 两种「换名字」逃逸（复审 2026-10-08 补）：**短名执行体**（`bin/py`、`bin/py3`）与
#   **以变量间接**（`{{ quant_python_cmd }}`——变量名含 `python`）⇒ 已一并纳入判据（见下）。
# ---------------------------------------------------------------------------

_SHARED_VENV_LITERAL = "shared/venv"
_INTERP_BASENAME_RE = re.compile(r"^(?:python|py)[0-9.]*$")
_JINJA_PY_VAR_RE = re.compile(r"\{\{[^}]*[Pp]ython[^}]*\}\}")


def _shellish_tokens(line: str) -> list[str]:
    """极简分词：单/双引号内保持完整（引号剥掉），其余按空白切。

    ⚠ 不用 `shlex`（在 `{{ }}` / `$(…)` / 中文标点上失真）——本判据只需「引号内不被空白切碎」
    这一条性质，自写反而可控。
    """
    toks: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote is not None:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "'\"":
            quote = ch
        elif ch.isspace():
            if buf:
                toks.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        toks.append("".join(buf))
    return toks


def _code_tokens(text: str) -> list[tuple[int, str]]:
    """（行号, token）——仅**非注释行**（注释/文案引用历史形态属说明面，不得假红）。"""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        out.extend((lineno, tok) for tok in _shellish_tokens(line))
    return out


def _interpreter_tokens(text: str) -> list[tuple[int, str]]:
    """basename 形如 `python*` / `py*` 的 token（`x.py` 因 basename 是 `x.py` 而不落网 ⇒ 无假红）。"""
    return [(ln, tok) for ln, tok in _code_tokens(text)
            if _INTERP_BASENAME_RE.match(tok.rsplit("/", 1)[-1])]


def _app_venv_refs(text: str) -> list[tuple[int, str]]:
    """token 含 `shared/venv` **字面**——**与执行体叫什么名字无关**（`bin/python`/`bin/py`/`bin/runit` 一律落网）。"""
    return [(ln, tok) for ln, tok in _code_tokens(text) if _SHARED_VENV_LITERAL in tok]


def _jinja_py_refs(text: str) -> list[tuple[int, str]]:
    """jinja 变量名含 `python`（`{{ quant_python_cmd }}`）——token 化会把 `{{`/`}}` 切碎，故按行扫。"""
    return [(ln, m.group(0))
            for ln, line in enumerate(text.splitlines(), 1) if not line.strip().startswith("#")
            for m in _JINJA_PY_VAR_RE.finditer(line)]


def test_playbook_interpreters_are_all_system_python3() -> None:
    """⑻a：全文（非注释行）的解释器 token 必须**只有** `/usr/bin/python3`。

    为什么单列：⑵c 只管阶段 4 段，而文件模式心跳支（阶段 8）一度有**同形**直读。判据＝依赖方向
    ——`shared/` 为 `0700 quant:quant`（`bootstrap.yml` 声明），deploy 身份物理上读不到应用 venv
    ⇒ 真机 rc=126；配合「门/断言恒跑」＝拦死每一次发布（2026-10-08 实测）。裸名（`python3`）依赖
    PATH 解析，属同族风险。
    """
    toks = _interpreter_tokens(_playbook_text())
    assert toks, "未扫到任何解释器 token ⇒ 提取器失效（守卫假绿）"
    bad = sorted({(ln, t) for ln, t in toks if t != _STAGE4_INTERPRETER})
    assert not bad, (
        f"解释器 token 出现非 `{_STAGE4_INTERPRETER}` 者 {bad}——deploy 身份读不到应用 venv"
        f"（0700）⇒ 真机 rc=126（2026-10-08 P0 同源）。需要 quant 身份请走 wrapper。"
    )


def test_playbook_has_no_app_venv_literal_in_executable_position() -> None:
    """⑻b：非注释行不得出现 `shared/venv` **字面**——**与 basename 无关**，堵「改名执行体」逃逸。"""
    refs = _app_venv_refs(_playbook_text())
    assert not refs, (
        f"非注释行出现应用 venv 路径字面 {refs}——凡需读 `shared/` 私域者一律走 wrapper（root/"
        f"sudo -u quant），playbook 内直引 `{_SHARED_VENV_LITERAL}` 即 rc=126 形态。"
    )


def test_playbook_has_no_python_named_jinja_interpreter_var() -> None:
    """⑻c：不得以「名字含 python 的变量」间接持有解释器（堵 `{{ quant_python_cmd }}` 逃逸）。"""
    refs = _jinja_py_refs(_playbook_text())
    assert not refs, (
        f"出现以 `python` 命名的 jinja 变量 {refs}——变量间接使静态判据失效；合规章法＝wrapper "
        f"变量（`{{{{ quant_*_cmd }}}}`，名不含 `python`；其值指向的 wrapper 属装位侧）。"
    )


def test_interpreter_guard_catches_renamed_and_indirect_forms() -> None:
    """守卫自身的守卫：**改名执行体** / **变量间接** / **裸名** 三类逃逸必须全被抓；注释不得假红。"""
    text = _playbook_text()
    assert not _app_venv_refs(text) and not _jinja_py_refs(text)  # 真文件绿
    assert {t for _, t in _interpreter_tokens(text)} == {_STAGE4_INTERPRETER}
    # ① 改名执行体：basename 不再是 python（`bin/runit`）⇒ 只靠 ⑻b（shared/venv 字面）落网
    renamed = text + "\n          ansible.builtin.shell: '{{ deploy_root }}'/shared/venv/bin/runit -V\n"
    assert _app_venv_refs(renamed), "改名执行体必须被 ⑻b 抓到（basename 无关）"
    # ② 变量间接：`{{ quant_python_cmd }}`
    indirect = text + "\n          ansible.builtin.shell: \"{{ quant_python_cmd }} -V\"\n"
    assert _jinja_py_refs(indirect), "变量间接必须被 ⑻c 抓到"
    # ③ 裸名执行体
    bare = text + "\n          ansible.builtin.shell: python3 -V\n"
    assert [t for _, t in _interpreter_tokens(bare) if t != _STAGE4_INTERPRETER] == ["python3"]
    # ④ 负向：注释行不得假红（说明面≠执行面）
    assert not _app_venv_refs(text + "\n        # 历史形态：'{{ deploy_root }}'/shared/venv/bin/python -V\n")
    assert not _jinja_py_refs(text + "\n        # 历史形态：{{ quant_python_cmd }} -V\n")
    # ⑤ 负向：`.py` 扩展名 / `pytest` / `copy` 不得因 basename 误报
    assert not _interpreter_tokens("      shell: run src/data_platform/migration_policy.py --check\n")
    assert not _interpreter_tokens("      shell: pytest -q && cp a b\n")


