"""回滚诚实性闸门（批 112）：任一回滚路径都必须校验「回滚后代码 ↔ 当前 schema」兼容。

## 为什么需要这条闸门

批 84 拆批产出（`flow/任务/批84-回滚诚实性与schema一致性.md`）：回滚只翻回旧代码，**schema 迁移
不回退**——旧代码配新 schema 若缺列，即「代码/schema 撕裂」，且此前回滚路径**无任何兼容校验**，
文案还输出「已自动回滚」的假话。本批给回滚路径加了 `quant-hbcheck compat` 兼容复验（`expected ⊆
actual`，有 miss 即 rc≠0），并把文案改成诚实词条。

本闸门只做**静态文件断言**（`yaml.safe_load` 后遍历任务名，不连环境），钉三件事：

1. **兼容复验可达性**：`rollback-tasks.yml` 含兼容复验任务，且 `rollback.yml`（独立手工回滚）与
   `release.yml` rescue(6-8)（自动回滚）**都** `include_tasks: rollback-tasks.yml`——两路共享同一
   复验，撤掉任一路的 include 或撤掉复验任务都会红。
2. **失败词条**：兼容复验失败必须输出固定词条 `SCHEMA_CODE_SPLIT`（+ 告警 + 「须人工」）——
   词条是诚实判定「代码/schema 撕裂」的唯一可检索锚点。
3. **假话已除**：`release.yml` 不得残留「expand-only 前提经 DDL 门恒跑检测保证」——该句与 DDL 门
   「只检不回滚安全」的语义不符（批 84 拆批声明 3②(ii) 明定为残留假保证）。

**覆盖边界（诚实声明）**：本闸门只保证「回滚路径的结构可达 + 词条锚点 + 假话已除」，不覆盖
`quant-hbcheck compat` 的**行为**（expected ⊆ actual 的比对正确性、fail-closed 语义）——那层靠
scratch schema + 真 wrapper 的往返回归（与批 83a 双态回归同手法）人工执行，见提交信息。
"""
from __future__ import annotations

from pathlib import Path

import yaml

_PLAYBOOKS = Path(__file__).resolve().parents[2] / "deploy" / "playbooks"
_RELEASE = _PLAYBOOKS / "release.yml"
_ROLLBACK = _PLAYBOOKS / "rollback.yml"
_ROLLBACK_TASKS = _PLAYBOOKS / "rollback-tasks.yml"

_COMPAT_NAME_MARK = "兼容复验"
_TOKEN = "SCHEMA_CODE_SPLIT"
_FALSE_CLAIM = "expand-only 前提经 DDL 门"
_ROLLBACK_TASKS_INCLUDE = "rollback-tasks.yml"


def _load(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data, f"{path.name} 解析为空＝假绿"
    return data


def _flatten(items) -> list[dict]:
    """展平 block/rescue/always 嵌套，产出全部叶子任务。"""
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if "block" in item:
            out.extend(_flatten(item.get("block")))
            out.extend(_flatten(item.get("rescue")))
            out.extend(_flatten(item.get("always")))
        else:
            out.append(item)
    return out


def _all_tasks(play: dict) -> list[dict]:
    tasks: list[dict] = []
    for section in ("pre_tasks", "tasks", "post_tasks"):
        tasks.extend(_flatten(play.get(section)))
    return tasks


def _rescue_68_tasks(play: dict) -> list[dict]:
    """release.yml 阶段 6-8（已切换）block 的 rescue 任务（自动回滚路径）。"""
    for item in play.get("tasks") or []:
        if isinstance(item, dict) and "阶段 6-8" in (item.get("name") or ""):
            return _flatten(item.get("rescue"))
    return []


def _task_body(task: dict) -> str:
    """取任务的 shell/command 体（两种键名：裸 `shell` 与全限定 `ansible.builtin.shell`）。"""
    for key in ("shell", "command", "ansible.builtin.shell", "ansible.builtin.command"):
        if task.get(key):
            return str(task[key])
    return ""


def _is_compat_task(task: dict) -> bool:
    name = task.get("name") or ""
    if _COMPAT_NAME_MARK not in name:
        return False
    return "compat" in _task_body(task)


def _includes_rollback_tasks(task: dict) -> bool:
    return (task.get("include_tasks") or "").strip().strip("\"'") == _ROLLBACK_TASKS_INCLUDE


def test_compat_reverification_reachable_from_both_rollback_paths():
    """① 兼容复验必须同时被 release.yml rescue(6-8) 与 rollback.yml 两路回滚复用。"""
    # rollback-tasks.yml（任务清单文件，非 playbook）恰含 1 个兼容复验任务
    rt = _flatten(_load(_ROLLBACK_TASKS))
    compat = [t for t in rt if _is_compat_task(t)]
    assert len(compat) == 1, (
        f"rollback-tasks.yml 应恰含 1 个兼容复验任务（name 含「{_COMPAT_NAME_MARK}」且 body 含 compat），"
        f"实得 {len(compat)}"
    )

    # rollback.yml（独立手工回滚）须 include rollback-tasks.yml
    rb = _all_tasks(_load(_ROLLBACK)[0])
    assert any(_includes_rollback_tasks(t) for t in rb), (
        "rollback.yml 须 include_tasks rollback-tasks.yml——独立回滚路径须复用同一兼容复验"
    )

    # release.yml rescue(6-8)（自动回滚）须 include rollback-tasks.yml
    rescue = _rescue_68_tasks(_load(_RELEASE)[0])
    assert any(_includes_rollback_tasks(t) for t in rescue), (
        "release.yml rescue(6-8) 须 include_tasks rollback-tasks.yml——自动回滚路径须复用同一兼容复验"
    )


def test_failure_wording_contains_schema_code_split():
    """② 兼容复验失败词条必须含固定锚点 SCHEMA_CODE_SPLIT。"""
    text = _ROLLBACK_TASKS.read_text(encoding="utf-8")
    assert _TOKEN in text, (
        f"rollback-tasks.yml 的兼容复验失败词条须含 `{_TOKEN}`——"
        "该词条是诚实判定「代码/schema 撕裂」的唯一可检索锚点（+ 告警 + 须人工）"
    )


def test_release_has_no_expand_only_false_claim():
    """③ release.yml 不得残留「expand-only 前提经 DDL 门」假话。"""
    text = _RELEASE.read_text(encoding="utf-8")
    assert _FALSE_CLAIM not in text, (
        f"release.yml 不得残留 `{_FALSE_CLAIM}`——该句与 DDL 门「只检不回滚安全」的语义不符"
        "（批 84 拆批声明 3②(ii) 明定为残留假保证）"
    )


# ---------------------------------------------------------------------------
# 反证钉（守卫自身的守卫——在副本上注入，不改真实文件）
# ---------------------------------------------------------------------------

def test_guard_catches_removed_compat_and_restored_false_claim():
    """撤掉兼容复验任务 / 加回假话 ⇒ 判据必须变红。"""
    rt_text = _ROLLBACK_TASKS.read_text(encoding="utf-8")
    rel_text = _RELEASE.read_text(encoding="utf-8")

    # 真文件：词条在、假话不在（守卫绿）
    assert _TOKEN in rt_text
    assert _FALSE_CLAIM not in rel_text

    # ② 撤掉词条 ⇒ 词条检测变红
    stripped = rt_text.replace(_TOKEN, "TOKEN_REMOVED")
    assert _TOKEN not in stripped, "注入未生效 ⇒ 本用例是假绿"

    # ① 撤掉兼容复验任务（整段 YAML 移除）⇒ 兼容任务计数归零（守卫会红）
    docs = yaml.safe_load(rt_text)
    assert len([t for t in docs if _is_compat_task(t)]) == 1, "真文件应恰 1 个兼容复验任务"
    kept = [t for t in docs if not _is_compat_task(t)]
    assert not [t for t in kept if _is_compat_task(t)], "撤掉后兼容任务须归零（守卫会红）"

    # ③ 加回假话 ⇒ 假话检测变红
    restored = rel_text + f"\n# {_FALSE_CLAIM}\n"
    assert _FALSE_CLAIM in restored, "注入未生效 ⇒ 本用例是假绿"
