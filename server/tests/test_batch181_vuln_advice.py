"""批181 守门：CVE 修复建议读侧综合——fix 与 latest 互不冒充。"""
import pytest

from src.quant_common.depjudge import vuln_advice


@pytest.mark.parametrize("cur,latest,fix,constraint,want_code,want_target", [
    ("3.0", "5.0", ["4.0"], None, "fixable", "4.0"),
    ("3.0", "5.0", ["4.0"], ">=3,<5", "fixable", "4.0"),
    ("3.0", "5.0", ["4.0"], ">=3,<4", "blocked_by_constraint", "4.0"),
    ("3.0", "3.5", ["4.0"], None, "not_in_index_yet", "4.0"),
    ("3.0", "5.0", [], None, "no_fix", None),
    ("4.0", "5.0", ["4.0"], None, "already_fixed", None),
    ("3.0", "5.0", ["4.1", "4.0"], None, "fixable", "4.0"),
    ("3.0", "5.0", ["4.1", "4.5"], ">=3,<4.2", "fixable", "4.1"),
    ("3.0", "5.0", ["4.1", "4.5"], ">=3,<4.0", "blocked_by_constraint", "4.1"),
    ("3.0", None, ["4.0"], None, "fixable", "4.0"),
    ("nope", "5.0", ["4.0"], None, "unknown", None),
])
def test_vuln_advice(cur, latest, fix, constraint, want_code, want_target):
    code, target, advice = vuln_advice(cur, latest, fix, constraint)
    assert code == want_code and target == want_target, (code, target, advice)
    assert isinstance(advice, str) and advice


def test_not_in_index_yet_does_not_suggest_downgrade():
    """fix > latest 时只提示「索引未见」，target 仍是 fix（不改成 latest）。"""
    code, target, _ = vuln_advice("3.0", "3.5", ["4.0"], None)
    assert code == "not_in_index_yet" and target == "4.0"


def test_priority_refresh_names():
    """优先集合只收「有 fix、未修复、索引未见」的包。"""
    from src.quant_common.depjudge import priority_refresh_names
    findings = [
        {"pkg": "a", "fix_version": "4.0"},   # 入选
        {"pkg": "b", "fix_version": "4.0"},   # 已修复
        {"pkg": "c", "fix_version": ""},      # 无 fix
        {"pkg": "d", "fix_version": "4.0"},   # latest 已见
        {"pkg": "e", "fix_version": "4.0"},   # latest 缺失 -> 入选
        {"pkg": "z", "fix_version": "4.0"},   # 不在安装集
    ]
    pip = {"a": "3.0", "b": "4.1", "c": "3.0", "d": "3.0", "e": "3.0"}
    latest = {"a": "3.5", "b": "4.1", "d": "4.0"}
    assert priority_refresh_names(findings, pip, latest) == ["a", "e"]


def test_findings_never_flow_into_latest():
    """铁律：finding 的 fix_version 只进 security_finding，不得写 latest_version。"""
    import inspect
    from src.health_monitor import security_collector as sc
    seg = inspect.getsource(sc).split("def _upsert_findings")[1].split("\ndef ")[0]
    assert "latest_version" not in seg

