"""批183d 守门：同名跨源包（pip tzdata vs rpm tzdata）判定不互相覆盖。

根因：原判定表按 `_norm_key(name)` 单键构建，OS 行后追加 ⇒ 覆盖 pip 行 ——
pip tzdata 的 managed_by 被写成 repo、latest 仍是 PyPI 版本，页面报「有新版但
dnf 无更新」（实测误报）。
"""
import inspect


def _rows():
    return [
        {"name": "tzdata", "cur": "2026.4", "latest": "2026.6", "managed_by": "pypi",
         "group": "runtime", "judge_class": "actionable", "judge_why": "PyPI 可升 2026.6"},
        {"name": "tzdata", "cur": "2026a", "latest": None, "managed_by": "repo",
         "group": "os", "judge_class": "current", "judge_why": ""},
    ]


def test_same_name_across_sources_not_overwritten():
    from src.health_monitor.security_collector import judged_map_of
    m = judged_map_of(_rows())
    assert m[("pip", "tzdata")][1] == "pypi", "pip 行被 OS 行覆盖（回归）"
    assert m[("pip", "tzdata")][0] == "2026.6"
    assert m[("system", "tzdata")][1] == "repo"
    assert m[("rpm", "tzdata")][1] == "repo"


def test_upsert_lookup_is_source_scoped():
    import src.health_monitor.security_collector as sc
    s = inspect.getsource(sc._upsert_components)
    assert "(source, _norm_key(name))" in s


def test_no_name_only_judged_map_left():
    """不得再有「单键按包名」的判定表构建（跨源覆盖的唯一入口）。"""
    import src.health_monitor.security_collector as sc
    text = inspect.getsource(sc)
    assert "judged_map_of(" in text
    assert "_norm_key(r[\"name\"]): (r.get(\"latest\")" not in text
