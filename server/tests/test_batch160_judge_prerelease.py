"""批 160：judge 预发布过滤（A）+ 孤儿行退役语义（B 由 upsert 侧 SQL 承载，此处钉 A 的判定语义）。"""
from __future__ import annotations

from src.quant_common.depjudge import judge


def _j(cur, latest, constraint=None):
    return judge(cur, constraint, latest, None, True, "pypi")


def test_rc_latest_not_actionable():
    c, w = _j("5.6.3", "5.7.0rc1")
    assert c == "current" and "预发布" in w, (c, w)


def test_beta_and_dev_not_actionable():
    for lat in ("0.8.0rc2", "0.24.4b1", "1.0.dev6", "7.0.0b1"):
        c, _ = _j("0.7.1", lat)
        assert c == "current", lat


def test_stable_latest_still_actionable():
    c, w = _j("3.37.0", "3.42.0")
    assert c == "actionable" and "3.42.0" in w


def test_stable_suffix_not_misjudged():
    """b/rc 后须跟数字才算预发布——防 ab1 之类的稳定版名误伤。"""
    c, _ = _j("1.0.0", "1.0.1b2")
    assert c == "current"
    c2, _ = _j("1.0", "2.0")
    assert c2 == "actionable"
