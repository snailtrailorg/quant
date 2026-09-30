"""部署同步腿 ssh 加固闸门：synchronize 任务必须继承 ssh 配置并校验主机键。

## 为什么需要这条闸门

`ansible.posix.synchronize` 的 **rsync 腿自起一个 ssh 进程**，与 ansible **连接腿不共享配置**。
模块两个默认值因此各自静默地做错事（2026-09-30 实查 + 修复）：

1. **配置静默丢失（功能性）**：默认 `use_ssh_args: false` → rsync 腿拿不到 inventory 的
   `ansible_ssh_args` / `ansible_ssh_common_args`。staging 彩排走真 ssh，该腿 ssh 只读系统配置，
   任何系统级异常（如本仓控制机 `/etc/ssh/ssh_config.d/*` 属主异常）就直接炸同步——
   而连接腿毫无问题，表现为「preflight 全绿、阶段 2 秒挂」的分裂现场，极难归因。
   模块源码 `modules/synchronize.py:577` 把 `_ssh_args` **追加**在 `-S none` 之后
   （`:566`），故 `use_ssh_args: true` 与「禁连接复用」的 `-S none` **无冲突**，可安全开启。

2. **安全静默降级（缺口）**：默认 `verify_host: false`（`modules/synchronize.py:574`）**硬加**
   `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`——把 TOFU 完全绕过。
   于是同一条管道两条腿两套策略：连接腿按 inventory 走 `accept-new`，rsync 腿什么都不校验。
   传输的正是**即将上线的代码工件**，这条腿的完整性反而没人看。

两件事都**没有任何 pytest / 前端构建 / 运行时信号**看得见：加闸门前 `deploy/tests/` 下
无一条断言涉及 synchronize。故按本仓「断言守门」惯例钉死，防回归。

## 钉两段

1. **每个 synchronize 任务**须显式 `use_ssh_args: true` + `verify_host: true`。
   递归扫 `block` / `rescue` / `always`，覆盖嵌套任务；扫 `deploy/playbooks/` 全目录，
   覆盖未来新增的 playbook（bootstrap/rollback 同律）。
2. **真源非空 + 反向禁止**：至少存在一个 synchronize 任务（防任务被删后空集假绿）；
   且 `use_ssh_args: false` / `verify_host: false` 不得显式出现在任何 playbook 里
   （防有人把降级写回去）。

**覆盖边界（诚实声明）**：本闸门只钉「任务参数的显式声明」这一层，**不**验证 rsync 腿
实际拼出的 ssh 命令行、也**不**验证 accept-new 真的生效——那层靠彩排实跑钉：
2026-09-30 staging 彩排阶段 2（白名单 7 切片 + web/dist）全绿即实证，日志见
`deploy/tests/logs/rehearsal-20260930.log`。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_PLAYBOOKS = _REPO / "deploy" / "playbooks"

# synchronize 的两种写法（短名需 collections 解析；本仓用全名，两者都认）
_SYNC_MODULES = ("ansible.posix.synchronize", "synchronize")

# 显式声明为「关」= 降级写回，一律红
_FORBIDDEN_DECLS = ("use_ssh_args: false", "verify_host: false")


def _iter_tasks(node):
    """递归产出任务 dict（下探 block/rescue/always 嵌套）。"""
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
        return
    if not isinstance(node, dict):
        return
    for key in ("block", "rescue", "always"):
        if key in node:
            yield from _iter_tasks(node[key])
    yield node


def _sync_args(task: dict):
    """任务若是 synchronize，产出 (模块名, 参数 dict)；否则 (None, None)。"""
    for mod in _SYNC_MODULES:
        if mod in task:
            args = task[mod]
            return mod, (args if isinstance(args, dict) else {})
    return None, None


def _iter_sync_tasks():
    """产出 (playbook 相对路径, 行号未知的任务名, 模块名, 参数 dict)。"""
    for path in sorted(_PLAYBOOKS.rglob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, list):
            continue
        for play in doc:
            if not isinstance(play, dict):
                continue
            for section in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                for task in _iter_tasks(play.get(section)):
                    mod, args = _sync_args(task)
                    if mod:
                        name = task.get("name", "(无名任务)")
                        yield path.relative_to(_REPO), name, mod, args


@pytest.mark.skipif(not _PLAYBOOKS.is_dir(), reason="无 deploy/playbooks（非本仓布局）")
def test_every_synchronize_task_declares_ssh_hardening():
    """每个 synchronize 任务须 `use_ssh_args: true` + `verify_host: true`（缺失即红）。"""
    offenders = []
    total = 0
    for rel, name, mod, args in _iter_sync_tasks():
        total += 1
        for key in ("use_ssh_args", "verify_host"):
            if args.get(key) is not True:
                offenders.append(
                    f"{rel}: 任务「{name}」({mod}) 的 {key}={args.get(key)!r} —— 须显式为 true"
                )
    assert total > 0, (
        f"{_PLAYBOOKS} 下未找到任何 synchronize 任务 —— 真源失效（任务被删/改名/挪走？），"
        "本闸门形同虚设"
    )
    assert not offenders, (
        "同步腿 ssh 加固缺失（rsync 腿不共享 ansible 连接腿的 ssh 配置）:\n  "
        + "\n  ".join(offenders)
        + "\n→ 缺 use_ssh_args=true：rsync 腿拿不到 inventory 的 ssh 参数（配置静默丢失）；"
          "\n→ 缺 verify_host=true：模块硬加 StrictHostKeyChecking=no + UserKnownHostsFile=/dev/null，"
          "TOFU 被绕过（安全静默降级）。\n"
          "  依据：ansible.posix modules/synchronize.py:574 / :577。"
    )


def test_synchronize_ssh_hardening_not_explicitly_switched_off():
    """反向：`use_ssh_args: false` / `verify_host: false` 不得显式出现（防降级写回）。"""
    offenders = []
    for path in sorted(_PLAYBOOKS.rglob("*.yml")):
        for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0]
            for decl in _FORBIDDEN_DECLS:
                if decl in line:
                    offenders.append(f"{path.relative_to(_REPO)}:{i}: {raw.strip()[:100]}")
    assert not offenders, (
        "同步腿 ssh 加固被显式关闭（降级写回）:\n  " + "\n  ".join(offenders)
    )
