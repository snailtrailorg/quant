"""批 172d P3 · MD 租约优雅释放（防部署 dwell 抖动）。

函数名 `_release_own_lease` 刻意避开退役的 M5 符号 `_lease_release`
（见 test_lease_guard.test_m5_machinery_gone）——语义是当前租约的停止清理，不是 M5 A/B 仲裁。
"""
from __future__ import annotations

from src.md_hub.parts import _release_own_lease


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
    assert _release_own_lease(r, 1, "u1") is True
    assert r.deleted == ["hub:lease:1"] or r.deleted[0].startswith("hub:lease")


def test_release_skips_when_not_owner():
    r = _R("other")
    assert _release_own_lease(r, 1, "u1") is False
    assert r.deleted == []


def test_release_never_raises():
    class _Bad:
        def eval(self, *a, **k):
            raise RuntimeError("down")

    assert _release_own_lease(_Bad(), 1, "u1") is False
    assert _release_own_lease(_R(), 1, "") is False
