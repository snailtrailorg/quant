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
