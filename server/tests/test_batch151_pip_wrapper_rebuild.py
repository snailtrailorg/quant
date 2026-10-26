# -*- coding: utf-8 -*-
"""批 151 · quant-pip-wrapper rebuild 子命令——文件级断言（模式同 test_deploy_dnf_wrapper）。

批 150 彩排红实证：release 管道只按 lock 装包不换解释器，staging venv 3.10 拒装
Requires-Python>=3.11 的 numpy 2.4.6——venv 解释器升级无正道。批 151 扩 rebuild：
备份→重建→工具链→按 release 树 lock 装配（与 install 同链），失败 .bak 兜底回滚。

wrapper 是 sudoers 白名单命令（改内容=提权面）——pytest 视野外，文件级钉住行为契约。
"""
from pathlib import Path

_WRAPPER = Path(__file__).resolve().parents[2] / "deploy" / "wrappers" / "quant-pip-wrapper"


def _src() -> str:
    return _WRAPPER.read_text(encoding="utf-8")


class TestRebuildContract:
    def test_subcommand_in_usage_and_case(self):
        s = _src()
        assert "rebuild <python_path> <release_id>" in s
        assert s.count("  rebuild)") == 1

    def test_strict_python_path_whitelist(self):
        s = _src()
        assert "/usr/bin/python3\\.[0-9]+$" in s

    def test_release_id_arg_position(self):
        s = _src()
        assert 'if [ "$sub" = "rebuild" ]; then' in s
        assert "release_id=${3:-}" in s

    def test_pip_precheck_exempts_rebuild(self):
        s = _src()
        assert '[ "$sub" != "rebuild" ]' in s

    def test_flow_backup_rebuild_tools_install(self):
        s = _src()
        body = s[s.index('if [ "$sub" = "rebuild" ];'):]
        i_bak = body.index("bak-$(date")
        i_venv = body.index("-m venv")
        i_tools = body.index("install -q -U pip setuptools wheel")
        i_install = body.index('install --require-hashes -i "$INDEX" -r "$REQ_STAGED"')
        assert i_bak < i_venv < i_tools < i_install

    def test_failure_rollback_on_every_step(self):
        s = _src()
        first = s.index('if [ "$sub" = "rebuild" ];')
        body = s[s.index('if [ "$sub" = "rebuild" ];', first + 10):].split('if [ "$sub" = "dry-run" ]')[0]
        assert body.count("回滚") >= 3

    def test_body_after_req_staged_construction(self):
        s = _src()
        first = s.index('if [ "$sub" = "rebuild" ];')
        body_pos = s.index('if [ "$sub" = "rebuild" ];', first + 10)
        assert s.index("REQ_STAGED=") < body_pos

    def test_exit0_after_success(self):
        s = _src()
        first = s.index('if [ "$sub" = "rebuild" ];')
        body = s[s.index('if [ "$sub" = "rebuild" ];', first + 10):].split('if [ "$sub" = "dry-run" ]')[0]
        assert "exit 0" in body
