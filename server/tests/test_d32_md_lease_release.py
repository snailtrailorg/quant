"""批 172d P3 · MD 租约优雅释放（防部署 dwell 抖动）。"""
from __future__ import annotations

from src.md_hub.parts import _lease_release


class _R:
    def __init__(self, owner="u1"):
        self.owner = owner
        self.deleted = []

    def eval(self, lua, n, key, uuid_):
        if self.owner == uuid_:
            self.deleted.append(key)
            return 1
        return 0


def test_release_only_own_lease():
    r = _R("u1")
    assert _lease_release(r, 1, "u1") is True
    assert r.deleted == ["hub:lease:1"] or r.deleted[0].startswith("hub:lease")


def test_release_skips_when_not_owner():
    r = _R("other")
    assert _lease_release(r, 1, "u1") is False
    assert r.deleted == []


def test_release_never_raises():
    class _Bad:
        def eval(self, *a, **k):
            raise RuntimeError("down")

    assert _lease_release(_Bad(), 1, "u1") is False
    assert _lease_release(_R(), 1, "") is False
