"""批179 守门：latest 按实际安装集补齐（组件列表不再因台账切面缺行而显示"无更新版本"）。"""
from src.health_monitor.security_collector import _fill_missing_latest


def _row(name, cur, latest=None, jc="unknown"):
    return {"name": name, "cur": cur, "latest": latest, "managed_by": "pypi",
            "group": "runtime", "judge_class": jc, "judge_why": "", "has_update": "no"}


def test_missing_row_filled_from_installed():
    judged = []
    calls = []
    def fetch(name, force=False):
        calls.append((name, force))
        return {"versions": ["2.10", "4.0.1", "4.1"]}
    n = _fill_missing_latest(judged, {"soupsieve": "2.10"}, {}, set(), fetch=fetch)
    assert n == 1 and calls == [("soupsieve", True)]
    row = judged[0]
    assert row["latest"] == "4.1" and row["has_update"] == "yes"
    assert row["judge_class"] == "actionable"


def test_null_latest_refetched():
    judged = [_row("soupsieve", "2.10")]
    calls = []
    def fetch(name, force=False):
        calls.append(force)
        return {"versions": ["4.1"]}
    _fill_missing_latest(judged, {"soupsieve": "2.10"}, {}, set(), fetch=fetch)
    assert calls == [True] and judged[0]["latest"] == "4.1"


def test_stale_cache_refetched_when_latest_le_current():
    """缓存里最新 <= 已装版本 → 视为落后，强制重查（发布后 7 天窗口问题）。"""
    judged = [_row("soupsieve", "4.1", latest="2.10", jc="current")]
    calls = []
    def fetch(name, force=False):
        calls.append(force)
        return {"versions": ["4.1", "4.2"]}
    _fill_missing_latest(judged, {"soupsieve": "4.1"}, {}, set(), fetch=fetch)
    assert calls == [True] and judged[0]["latest"] == "4.2"


def test_up_to_date_row_not_refetched():
    judged = [_row("pandas", "3.0.0", latest="3.1.0", jc="actionable")]
    def fetch(name, force=False):
        raise AssertionError("不应重查")
    assert _fill_missing_latest(judged, {"pandas": "3.0.0"}, {}, set(), fetch=fetch) == 0


def test_fetch_failure_does_not_raise():
    def fetch(name, force=False):
        raise RuntimeError("network")
    judged = []
    assert _fill_missing_latest(judged, {"x": "1.0"}, {}, set(), fetch=fetch) == 0
    assert judged == []
