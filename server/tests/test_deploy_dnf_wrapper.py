"""批 120 步 4 P0-4 · quant-dnf-security 部署守卫（四方一致性文件级断言）。

## 为什么需要这条闸门（步 4 同判 A 线实证）

批 120 部署侧（1824f64）为 dnf --security 数据文件通道建立了 wrapper
（`deploy/wrappers/quant-dnf-security`），但**零 pytest 断言**——声称的 35 例全绿
是既有测试，不覆盖本批新增件。wrapper 是 sudoers 白名单命令（改内容=提权面）、
sudoers/release.yml/bootstrap.yml 分居三处，任一漂移（漏装位/漏白名单/漏任务）
都无任何东西会响——模式与 test_deploy_blast_radius 同源：**部署链文件在
pytest 视野外，必须显式入仓钉住**。

## 钉四段

1. **wrapper 源码**：严参校验（`[ $# -eq 0 ]`）/`dnf --security` 调用/三态落文件
   （ok pending=rc100、ok none=rc0、degraded=其他）/全路径 `exit 0`（不阻部署）。
2. **sudoers 模板**：`deploy/templates/sudoers-quant-deploy.j2` 含 quant-dnf-security
   白名单行（进 QUANT_DEPLOY_ROOT alias）。
3. **release.yml**：含调用任务（post_tasks 阶段）。
4. **bootstrap.yml**：含装位行（wrapper 族 loop 项）——任一文件改动漂移即红。

**覆盖边界（诚实声明）**：文件级静态断言只保证「四方一致不漂移 + wrapper 行为契约
在源码里成立」；wrapper 真跑行为（dnf 三态真解析）靠沙箱失败注入（deploy/ 六场景）
与 staging 彩排，不在 pytest 范围。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "deploy" / "wrappers" / "quant-dnf-security"
_SUDOERS = _REPO / "deploy" / "templates" / "sudoers-quant-deploy.j2"
_RELEASE = _REPO / "deploy" / "playbooks" / "release.yml"
_BOOTSTRAP = _REPO / "deploy" / "playbooks" / "bootstrap.yml"

NAME = "quant-dnf-security"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestWrapperSource:
    """第 1 段：wrapper 本体行为契约（静态源码断言）。"""

    def test_strict_arg_check(self):
        """严参校验：不收任何参数（sudoers `*` 吃空格，夹带参数即拒）。"""
        src = _read(_WRAPPER)
        assert re.search(r"\[ \$# -eq 0 \]", src), "缺严参校验 `[ $# -eq 0 ]`（sudoers 注入面）"

    def test_calls_dnf_security(self):
        """走 dnf check-update --security（root 特权动作本体）。"""
        src = _read(_WRAPPER)
        assert "check-update --security" in src

    def test_three_state_json_written(self):
        """三态全落文件：ok 无待更（pending_count 0）/ok 有待更（rc=100 解析包清单）/
        degraded（其他退出码显式落状态）——绝不静默空表（假绿红线）。"""
        src = _read(_WRAPPER)
        assert '\\"status\\": \\"ok\\"' in src and '\\"pending_count\\": 0' in src
        assert '\\"status\\": \\"degraded\\"' in src
        # 数据文件路径与采集器 DNF_SECURITY_FILE 同构
        assert "dnf-security.json" in src

    def test_all_paths_exit_zero(self):
        """三态路径全部 exit 0——degraded 是显式状态非失败，不阻部署。"""
        src = _read(_WRAPPER)
        exits = re.findall(r"^\s*exit\s+(\d+)\s*$", src, re.M)
        assert exits and all(rc == "0" for rc in exits), f"存在非零 exit: {exits}"

    def test_dnf_exit_100_is_pending_not_failure(self):
        """rc=100 是 dnf「有待更」官方语义 ⇒ ok+包清单，不当 degraded。"""
        src = _read(_WRAPPER)
        assert re.search(r'\[ "\$rc" -eq 100 \]', src), "缺 rc=100 有待更分支"


class TestFourWayConsistency:
    """第 2-4 段：sudoers/release/bootstrap 与 wrapper 的一致性（漂移即红）。"""

    def test_sudoers_whitelist_line(self):
        src = _read(_SUDOERS)
        assert f"/usr/local/sbin/{NAME}" in src, \
            f"sudoers 模板缺 {NAME} 白名单行（deploy 无 sudo 通道 ⇒ release 任务必失败）"
        # 必须在 QUANT_DEPLOY_ROOT alias 内（root 属主族），不是裸行
        assert re.search(r"Cmnd_Alias QUANT_DEPLOY_ROOT.*", src)

    def test_release_yml_has_task(self):
        src = _read(_RELEASE)
        assert NAME in src, "release.yml 缺 dnf 安全快照任务（数据文件不更新 ⇒ 采集器恒 degraded）"
        # 调用走 group_vars 注入的命令变量（quant_dnf_security_cmd），非裸命令
        assert "quant_dnf_security_cmd" in src

    def test_bootstrap_yml_installs_wrapper(self):
        src = _read(_BOOTSTRAP)
        assert NAME in src, "bootstrap.yml 缺 wrapper 装位行（新机漏装 ⇒ release 任务必失败）"

    def test_wrapper_file_exists(self):
        assert _WRAPPER.is_file(), f"缺 wrapper 本体: {_WRAPPER}"
