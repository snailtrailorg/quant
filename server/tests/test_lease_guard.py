"""hub boot 守卫/租约单测（批 66a a′——M5 退役后形态，D26 §4.1）。

覆盖：_lease_acquire 三态（NX 成功 INCR / 真让位 -1 / 网络问题 0）/ 不写 active_instance
（M5 仲裁退役钉）/ _lease_boot 让位 exit 3 + 重试耗尽 exit 4 / _cred_hash canonical 稳定性 /
M5 机关残留清零（防复活钉）。续租 Lua（CAS EXPIRE）语义由真 Valkey 集成验证。
"""
from unittest.mock import ANY, MagicMock, patch

import pytest


# ——— _lease_acquire（a′：SET NX EX 30 + 守卫通过后 INCR）———

def test_acquire_ok_incr_gen():
    r = MagicMock()
    r.set.return_value = True   # NX 成功
    r.incr.return_value = 7
    from src.md_hub.parts import _lease_acquire
    ok, uuid_, gen = _lease_acquire(r)
    assert ok is True and gen == 7
    # a′ 唯一双活 fencing=NX——kwargs 钉死（丢 nx=True 变普通 SET 则 lease 可被无条件覆盖，盲审 A-P2）
    r.set.assert_called_once_with("hub:lease", ANY, nx=True, ex=30)
    r.incr.assert_called_once()   # 只 SET lease（NX EX 30），无 active_instance 写（M5 退役钉）


def test_acquire_lease_held_surrender():
    r = MagicMock()
    r.set.return_value = False   # NX 失败（键被持）
    r.get.return_value = "other-uuid"
    from src.md_hub.parts import _lease_acquire
    ok, uuid_, gen = _lease_acquire(r)
    assert ok is False and gen == -1   # 真让位


def test_acquire_storage_unreachable_retry():
    r = MagicMock()
    r.set.side_effect = Exception("down")
    from src.md_hub.parts import _lease_acquire
    ok, uuid_, gen = _lease_acquire(r)
    assert ok is False and gen == 0   # 网络问题稍后重试（不退出）


def test_acquire_gen_incr_fail_retry():
    r = MagicMock()
    r.set.return_value = True
    r.incr.side_effect = Exception("down")
    from src.md_hub.parts import _lease_acquire
    ok, uuid_, gen = _lease_acquire(r)
    assert ok is False and gen == 0


# ——— _lease_boot（3 次重试；真让位 exit 3；耗尽 exit 4）———

def test_boot_success_first_try():
    r = MagicMock()
    r.set.return_value = True
    r.incr.return_value = 3
    from src.md_hub.parts import _lease_boot
    uuid_, gen = _lease_boot(r)
    assert gen == 3


def test_boot_surrender_exit3_writes_marker():
    r = MagicMock()
    r.set.side_effect = [False, "marker-ok"]   # NX 失败；surrender 标记 SET 成功
    from src.md_hub.parts import _lease_boot
    with pytest.raises(SystemExit) as e:
        _lease_boot(r)
    assert e.value.code == 3
    assert r.set.call_count == 2   # lease NX + surrender 标记


def test_boot_retry_exhausted_exit4():
    r = MagicMock()
    r.set.side_effect = Exception("down")   # 持续网络问题 → (False, _, 0) ×3
    from src.md_hub.parts import _lease_boot
    with patch("src.md_hub.parts.time.sleep"), \
         patch("src.md_hub.parts.os._exit") as xe:
        _lease_boot(r)   # os._exit 是 syscall 非异常——patch 断言调用码（真退出会杀 pytest 进程）
    xe.assert_called_once_with(4)


# ——— _cred_hash（批 66a 凭证摘要——canonical json 稳定性）———

def test_cred_hash_key_order_insensitive():
    from src.md_hub.main import _cred_hash
    a = _cred_hash({"app_id": "A", "app_secret": "S"})
    b = _cred_hash({"app_secret": "S", "app_id": "A"})
    assert a == b and len(a) == 64   # sha256 hex


def test_cred_hash_content_sensitive():
    from src.md_hub.main import _cred_hash
    assert _cred_hash({"app_id": "A"}) != _cred_hash({"app_id": "B"})


# ——— M5 机关残留清零（防复活钉，D26 §4.1 退役边界）———

def test_m5_machinery_gone():
    import src.md_hub.main as m
    import src.md_hub.parts as p
    for attr in ("_read_intent", "_boot_dispatch", "_intent_poll"):
        assert not hasattr(m, attr), f"main.{attr} 应随 M5 退役"
    for attr in ("INTENT_KEY", "ACTIVE_INSTANCE_KEY", "_lease_acquire_guarded", "_lease_release"):
        assert not hasattr(p, attr), f"parts.{attr} 应随 M5 退役"
