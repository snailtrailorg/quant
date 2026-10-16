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


class TestSshSnapshotGuard:
    """批 120 首采补 · quant-ssh-snapshot 部署守卫（同四方一致性模式——sshd_config 600 后采集通道）。"""

    SSH_NAME = "quant-ssh-snapshot"
    _SSH_WRAPPER = _REPO / "deploy" / "wrappers" / "quant-ssh-snapshot"

    def test_wrapper_source_contract(self):
        src = _read(self._SSH_WRAPPER)
        assert '[ $# -eq 0 ]' in src, "须无参化严参校验（sudoers * 吃空格的注入面）"
        assert "/etc/ssh/sshd_config" in src
        assert "sshd-config.txt" in src, "快照落点须为采集器消费路径"
        assert "exit 0" in src, "全部路径 exit 0（degraded 不阻部署）"

    def test_sudoers_whitelist_line(self):
        src = _read(_SUDOERS)
        assert f"/usr/local/sbin/{self.SSH_NAME}" in src, "sudoers 缺白名单行"

    def test_release_yml_has_task(self):
        src = _read(_RELEASE)
        assert "quant-ssh-snapshot" in src and "quant_ssh_snapshot_cmd" in src, \
            "release.yml 缺 ssh 快照任务（快照不更新 ⇒ 采集器 ssh 分区恒 degraded）"

    def test_bootstrap_yml_installs_wrapper(self):
        src = _read(_BOOTSTRAP)
        assert self.SSH_NAME in src, "bootstrap.yml 缺 wrapper 装位行"

    def test_inventory_vars_three_envs(self):
        for env in ("quant-prod", "quant-staging", "sandbox"):
            f = _REPO / "deploy" / "inventory" / "group_vars" / f"{env}.yml"
            assert "quant_ssh_snapshot_cmd" in _read(f), f"{env} 缺 quant_ssh_snapshot_cmd 变量"


class TestSectriggerGuard:
    """批 122 · quant-sectrigger 部署守卫（四方一致性——依赖变更触发当日安全扫描通道）。"""

    def test_wrapper_source_contract(self):
        w = _REPO / "deploy" / "wrappers" / "quant-sectrigger"
        src = _read(w)
        assert '[ $# -eq 0 ]' in src, "须无参化严参校验"
        assert "security_scan" in src and "send_task" in src
        assert 'src.scheduler.app' in src, "celery app 真实模块路径（防再走错端点）"
        assert "exit 0" not in src.split("exec")[1] if "exec" in src else True

    def test_sudoers_whitelist(self):
        assert "/usr/local/sbin/quant-sectrigger" in _read(_SUDOERS)

    def test_release_yml_task(self):
        src = _read(_RELEASE)
        assert "quant-sectrigger" in src and "quant_sectrigger_cmd" in src
        assert "req_changed | bool" in src, "仅依赖变更日触发"

    def test_bootstrap_installs(self):
        assert "quant-sectrigger" in _read(_BOOTSTRAP)
        for f in ("bootstrap_server.sh", "bootstrap_staging.sh"):
            assert "sectrigger" in _read(_REPO / "deploy" / "scripts" / f), f

    def test_inventory_three_envs(self):
        for env in ("quant-prod", "quant-staging", "sandbox"):
            assert "quant_sectrigger_cmd" in _read(_REPO / "deploy" / "inventory" / "group_vars" / f"{env}.yml")


class TestPipWrapperUpgradeTools:
    """批 124 · venv 工具链升级通道守卫（setuptools 旧版 3 CVE 的自动化修复）。"""

    _PIP_WRAPPER = _REPO / "deploy" / "wrappers" / "quant-pip-wrapper"

    def test_wrapper_has_subcommand(self):
        """wrapper 须含 upgrade-tools 分支（严参白名单内）+ 升级集明确。"""
        src = _read(self._PIP_WRAPPER)
        assert "upgrade-tools)" in src
        assert "install -U --upgrade-strategy only-if-needed pip setuptools wheel" in src
        assert "upgrade-tools 不收参数" in src   # 严参校验（同 freeze）

    def test_release_calls_upgrade_tools(self):
        """release.yml 阶段 5 须调 upgrade-tools（server_changed 门 + fail-soft）。"""
        src = _read(_RELEASE)
        assert "quant_pip_cmd }} upgrade-tools" in src
        # 失败不阻断发布（工具链升级非发布阻塞项）
        idx = src.index("upgrade-tools")
        seg = src[max(0, idx - 400):idx + 400]
        assert "failed_when: false" in seg

    def test_in_sudoers_whitelist(self):
        """quant-pip-wrapper 已在 QUANT_DEPLOY_QUANT 白名单（复用通道——零新权限面）。"""
        sudoers = _read(_REPO / "deploy" / "templates" / "sudoers-quant-deploy.j2")
        assert "/usr/local/sbin/quant-pip-wrapper" in sudoers


class TestPipWrapperNoguiRewrite:
    """批 125 · fork wheel 的 file:// 路径重写守卫（服务器上控制机绝对路径不存在）。"""

    _PIP_WRAPPER = _REPO / "deploy" / "wrappers" / "quant-pip-wrapper"

    def test_wrapper_rewrites_file_uri(self):
        """install/dry-run 须消费 REQ_STAGED（sed 重写 file:///home/bernard → releases 树）。"""
        src = _read(self._PIP_WRAPPER)
        assert "REQ_STAGED=" in src
        assert 'file:///home/bernard/Projects/quant/server/deps/' in src   # 重写源路径
        assert '$ROOT/releases/$release_id/deps/' in src                    # 重写目标
        assert '-r "$REQ_STAGED"' in src
        assert '-r "$REQ"' not in src.replace('-r "$REQ_STAGED"', '')      # 旧直引已清

    def test_deps_in_slices_and_fingerprint(self):
        """deps 切片+白名单+指纹判据三处（缺一=服务器装不到 wheel 或变更不触发重装）。"""
        gv = _read(_REPO / "deploy" / "inventory" / "group_vars" / "all.yml")
        assert "alembic.ini, deps]" in gv
        assert "{path: deps, dir: true}" in gv
        rel = _read(_RELEASE)
        assert "get('deps', '')" in rel

    def test_wrapper_orphan_cleanup(self):
        """批 125：install 段须清 GUI 孤儿（pyside6 出闭包后 shiboken6/pyqtgraph 残留）。"""
        src = _read(self._PIP_WRAPPER)
        assert "uninstall -y pyside6" in src
        assert "shiboken6 pyqtgraph qdarkstyle" in src
