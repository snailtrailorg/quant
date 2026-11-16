"""批183d/184a 守门：同名跨域（pip tzdata vs 系统 tzdata）判定不互相覆盖。

根因：判定表原按 `_norm_key(name)` 单键构建，OS 行后追加 ⇒ 覆盖 pip 行 ——
pip tzdata 的 managed_by 被写成 repo、latest 仍是 PyPI 版本，页面报「有新版但
dnf 无更新」。批184a 进一步把键固定为 (域, 名)，域即 source（pip/os/binary）。
"""
import inspect


def _rows():
    return [
        {"name": "tzdata", "cur": "2026.4", "latest": "2026.6", "managed_by": "pypi",
         "group": "runtime", "judge_class": "actionable", "judge_why": "PyPI 可升 2026.6"},
        {"name": "tzdata", "cur": "2026a", "latest": None, "managed_by": "repo",
         "group": "os", "judge_class": "current", "judge_why": ""},
    ]


def test_same_name_across_domains_not_overwritten():
    from src.health_monitor.security_collector import judged_map_of
    m = judged_map_of(_rows())
    assert set(m) == {("pip", "tzdata"), ("os", "tzdata")}, m.keys()
    assert m[("pip", "tzdata")][0] == "2026.6"
    assert m[("pip", "tzdata")][1] == "actionable"
    assert m[("os", "tzdata")][0] is None
    assert m[("os", "tzdata")][1] == "current"


def test_judged_value_is_three_tuple_without_managed_by():
    """managed_by 是域的纯函数（_MANAGED_BY），不进判定契约——防写侧回环。"""
    from src.health_monitor.security_collector import judged_map_of
    v = judged_map_of(_rows())[("pip", "tzdata")]
    assert len(v) == 3


def test_managed_by_derived_by_domain():
    from src.health_monitor.security_collector import _MANAGED_BY
    assert _MANAGED_BY == {"pip": "pypi", "os": "repo", "binary": "none"}


def test_no_name_only_judged_map_left():
    """不得再有「单键按包名」的判定表构建（跨域覆盖的唯一入口）。"""
    import src.health_monitor.security_collector as sc
    text = inspect.getsource(sc)
    assert "judged_map_of(" in text
    assert "_norm_key(r[\"name\"]): (r.get(\"latest\")" not in text
