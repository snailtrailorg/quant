"""M5 租约/切换协议单测（批60 契约清单 C2——租约逻辑原零覆盖，补齐回归钉）。

覆盖：guarded Lua 三态返回值解析 / CAS DEL / _read_intent / boot 单判定 dispatch 分叉 /
normal 冷启 SET active_instance。Lua 脚本本身的正误由真 Valkey 集成验证（验收判据 3）。
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.md_hub.parts import (
    ACTIVE_INSTANCE_KEY, INTENT_KEY,
    _lease_acquire, _lease_acquire_guarded, _lease_release,
)
from src.md_hub.main import _boot_dispatch, _read_intent


# ——— _lease_acquire_guarded 三态（Python 侧对 Lua 返回值的解析）———

def test_guarded_success():
    r = MagicMock()
    r.eval.return_value = 5
    ok, uuid_, gen = _lease_acquire_guarded(r, expected_gen=5, target="quant2", uuid_="u1")
    assert ok is True and gen == 5
    r.eval.assert_called_once()


def test_guarded_pollution_returns_minus1():
    r = MagicMock()
    r.eval.return_value = -1
    ok, uuid_, gen = _lease_acquire_guarded(r, 5, "quant2", "u1")
    assert ok is False and gen == -1


def test_guarded_old_lease_returns_minus2():
    r = MagicMock()
    r.eval.return_value = -2
    ok, uuid_, gen = _lease_acquire_guarded(r, 5, "quant2", "u1")
    assert ok is False and gen == -2


def test_guarded_storage_unreachable_returns_0():
    r = MagicMock()
    r.eval.side_effect = Exception("down")
    ok, uuid_, gen = _lease_acquire_guarded(r, 5, "quant2", "u1")
    assert ok is False and gen == 0


# ——— _lease_release CAS DEL ———

def test_release_cas_ok():
    r = MagicMock()
    r.eval.return_value = 1
    assert _lease_release(r, "u1") is True


def test_release_cas_not_owner():
    r = MagicMock()
    r.eval.return_value = 0
    assert _lease_release(r, "u1") is False


# ——— _read_intent ———

def test_read_intent_ok():
    r = MagicMock()
    r.get.return_value = json.dumps({"snapshot": 3, "target": "quant2"})
    assert _read_intent(r) == {"snapshot": 3, "target": "quant2"}


def test_read_intent_absent():
    r = MagicMock()
    r.get.return_value = None
    assert _read_intent(r) is None


def test_read_intent_bad_json():
    r = MagicMock()
    r.get.return_value = "{bad"
    assert _read_intent(r) is None


# ——— _boot_dispatch 分叉 ———

def _intent_r(snapshot, target):
    r = MagicMock()
    r.get.side_effect = lambda k: (json.dumps({"snapshot": snapshot, "target": target})
                                   if k == INTENT_KEY else None)
    return r


def test_boot_target_goes_guarded():
    r = _intent_r(3, "quant2")
    with patch("src.md_hub.main._lease_acquire_guarded", return_value=(True, "uuid-b", 4)) as g:
        uuid_, gen = _boot_dispatch(r, "quant2")
    assert gen == 4
    g.assert_called_once()


def test_boot_not_target_exit6():
    r = _intent_r(3, "quant2")
    with pytest.raises(SystemExit) as e:
        _boot_dispatch(r, "quant")   # 本实例 quant ≠ target quant2
    assert e.value.code == 6


def test_boot_not_active_exit6():
    r = MagicMock()
    r.get.side_effect = lambda k: (None if k == INTENT_KEY else "quant2")   # intent 无，active=quant2
    with pytest.raises(SystemExit) as e:
        _boot_dispatch(r, "quant")
    assert e.value.code == 6


def test_boot_normal_cold_start():
    r = MagicMock()
    r.get.return_value = None   # intent 无，active 无
    with patch("src.md_hub.main._lease_boot", return_value=("uuid-a", 1)) as boot:
        uuid_, gen = _boot_dispatch(r, "quant")
    assert gen == 1
    boot.assert_called_once_with(r, "quant")


# ——— _lease_acquire normal 冷启 SET active_instance ———

def test_acquire_sets_active_instance():
    r = MagicMock()
    r.set.return_value = True   # NX 成功
    r.incr.return_value = 7
    ok, uuid_, gen = _lease_acquire(r, instance_name="quant")
    assert ok is True and gen == 7
    r.set.assert_any_call(ACTIVE_INSTANCE_KEY, "quant")


def test_acquire_no_active_when_no_instance_name():
    r = MagicMock()
    r.set.return_value = True
    r.incr.return_value = 7
    ok, uuid_, gen = _lease_acquire(r, instance_name="")
    assert ok is True and gen == 7
    # 未传 instance_name 时不应 SET active_instance
    assert not any(c[0][0] == ACTIVE_INSTANCE_KEY for c in r.set.call_args_list)


def test_acquire_surrender_when_lease_held():
    r = MagicMock()
    r.set.return_value = False   # NX 失败
    r.get.return_value = "other-uuid"
    ok, uuid_, gen = _lease_acquire(r)
    assert ok is False and gen == -1
