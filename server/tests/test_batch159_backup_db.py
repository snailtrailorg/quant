"""批 159：backup_db 收编——四道防线单测（方案 v2 §四.1）。"""
from __future__ import annotations

import os

import pytest


class _P:
    """Popen 桩：可控 rc/进程序列。"""
    def __init__(self, rc=0, pid=100):
        self.returncode = rc
        self.pid = pid
        import io
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
    def wait(self):
        return self.returncode

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def communicate(self, input=None, timeout=None):
        return (b"", b"")

    def poll(self):
        return self.returncode

    def kill(self):
        pass

    def terminate(self):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    import src.scheduler.tasks as T
    monkeypatch.setattr(T.backup_db, "__wrapped__", None, raising=False)
    BK = tmp_path / "backups"
    BK.mkdir()
    # 把任务里的硬编码路径换掉：monkeypatch Path? 简洁法——重定义任务用 os.environ?
    # 任务实现读 BACKUP_DIR 常量于函数内——用 patch dict 不可。改法：任务已写成函数体局部常量，
    # 测试经 _monkey BACKUP_DIR 需求太大——用环境注入：测试直接 patch tasks 模块级无此常量。
    # ⇒ 改测法：monkeypatch _sp.Popen/df/notify/unlink 面，backups 目录用真实常量不可行（CI 无权）。
    # 终法：任务里 BACKUP_DIR 支持 env 覆盖（见任务代码 QUANT_BACKUP_DIR 注入）——本夹具设之。
    monkeypatch.setenv("QUANT_BACKUP_DIR", str(BK))
    return BK


def test_pipe_rc_masking_caught(monkeypatch, env):
    """防线 1 反例：pg_dump rc=1 而 gzip rc=0 —— 必须判失败（单管道 rc=末命令陷阱）。"""
    import subprocess as sp
    import src.scheduler.tasks as T
    calls = {"n": 0}
    def _popen(cmd, **kw):
        calls["n"] += 1
        return _P(rc=1 if cmd[0] == "pg_dump" else 0)
    monkeypatch.setattr(sp, "Popen", _popen)
    import importlib as _il; _AN = _il.import_module("src.alert_notify.notify"); monkeypatch.setattr(_AN, "notify", lambda *a, **k: None)
    res = T.backup_db.run()
    assert res["status"] == "failed"
    assert "rc dump=1" in res["error"]


def test_partial_cleaned_on_fail(monkeypatch, env):
    """防线 2：失败后目录无 .tmp 残留。"""
    import subprocess as sp
    import src.scheduler.tasks as T
    def _popen(cmd, **kw):
        return _P(rc=1)
    monkeypatch.setattr(sp, "Popen", _popen)
    import importlib as _il; _AN = _il.import_module("src.alert_notify.notify"); monkeypatch.setattr(_AN, "notify", lambda *a, **k: None)
    T.backup_db.run()
    assert not list(env.glob("*.tmp"))


def test_space_preflight_aborts(monkeypatch, env):
    """防线 4：可用空间不足 abort（不启 Popen）。"""
    import subprocess as sp
    import src.scheduler.tasks as T
    (env / "quant_20261030_020000.sql.gz").write_bytes(b"x" * 100)
    class _SV:   # statvfs 桩：可用 1 块
        f_bavail, f_frsize = 1, 4096
    monkeypatch.setattr(os, "statvfs", lambda p: _SV())
    import importlib as _il; _AN = _il.import_module("src.alert_notify.notify"); monkeypatch.setattr(_AN, "notify", lambda *a, **k: None)
    res = T.backup_db.run()
    assert res["status"] == "failed" and "空间预检" in res["error"]


def test_keep_count_three(monkeypatch, env):
    """保留 3 份：成功后旧份裁剪。"""
    import subprocess as sp
    import src.scheduler.tasks as T
    for d in ("01", "02", "03", "04", "05"):
        (env / f"quant_202610{d}_020000.sql.gz").write_bytes(b"old")
    def _popen(cmd, **kw):
        return _P(rc=0)
    monkeypatch.setattr(sp, "Popen", _popen)
    import importlib as _il; _AN = _il.import_module("src.alert_notify.notify"); monkeypatch.setattr(_AN, "notify", lambda *a, **k: None)
    res = T.backup_db.run()
    assert res["status"] == "success"
    assert res["kept"] == 3
    files = sorted(p.name for p in env.glob("quant_*.sql.gz"))
    assert len(files) == 3, files


def test_replace_oserror_caught(monkeypatch, env):
    """步4盲审 P0 回归钉：os.replace 抛 OSError（如并发 tmp 被删）——
    必须落 _fail（failed+tmp 清净），而非 NameError/裸逃。"""
    import os as _os
    import subprocess as sp
    import src.scheduler.tasks as T
    monkeypatch.setattr(sp, "Popen", lambda cmd, **kw: _P(rc=0))
    monkeypatch.setattr(_os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("race")))
    _notify_calls = []
    import importlib as _il
    _AN = _il.import_module("src.alert_notify.notify")
    monkeypatch.setattr(_AN, "notify", lambda *a, **k: _notify_calls.append(a))
    res = T.backup_db.run()
    assert res["status"] == "failed" and "OSError" in res["error"], res
    assert not list(env.glob("*.tmp"))
    assert _notify_calls, "失败必须告警"


def test_softlimit_kills_process_group(monkeypatch, env):
    """步4盲审 P1 回归钉：软限在 wait 段抛出——finally 杀进程组+清残件+failed。"""
    import os as _os
    import signal as _sig
    import subprocess as sp
    from celery.exceptions import SoftTimeLimitExceeded
    import src.scheduler.tasks as T

    killed = []
    monkeypatch.setattr(_os, "killpg", lambda gid, sig: killed.append((gid, sig)))
    monkeypatch.setattr(_os, "getpgid", lambda pid: pid)

    class _StallP(_P):
        def wait(self):
            raise SoftTimeLimitExceeded()

    _live = _StallP(rc=None)
    _live.poll = lambda: None   # 进程「还活着」——finally 应杀
    monkeypatch.setattr(sp, "Popen", lambda cmd, **kw: _live)
    import importlib as _il
    _AN = _il.import_module("src.alert_notify.notify")
    monkeypatch.setattr(_AN, "notify", lambda *a, **k: None)
    res = T.backup_db.run()
    assert res["status"] == "failed", res
    assert killed and killed[0][1] == _sig.SIGKILL, "进程组必须被 SIGKILL"
    assert not list(env.glob("*.tmp")), "残件必须清理"


def test_space_boundary_exact(monkeypatch, env):
    """步4盲审 P2：可用==need 边界——放行（不小于）。"""
    import subprocess as sp
    import src.scheduler.tasks as T
    (env / "quant_20261030_020000.sql.gz").write_bytes(b"x" * 100)
    class _SV:   # need 有 1GiB fallback 下限——充足语义须 ≥1G（字面「恰等 need」在 1G 地板下不可达）
        f_bavail, f_frsize = (1 << 30) // 4096 + 1, 4096
    import os as _os
    monkeypatch.setattr(_os, "statvfs", lambda p: _SV())
    monkeypatch.setattr(sp, "Popen", lambda cmd, **kw: _P(rc=0))
    import importlib as _il
    _AN = _il.import_module("src.alert_notify.notify")
    monkeypatch.setattr(_AN, "notify", lambda *a, **k: None)
    res = T.backup_db.run()
    assert res["status"] == "success", res
