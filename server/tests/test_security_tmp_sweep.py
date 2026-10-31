"""扫描类临时残件合并清理（2026-10-31）：grype 孤儿 / sbom / 旧锁三件语义。"""
from __future__ import annotations

import os
import time
from pathlib import Path


def _mk(p: Path, age_s: int, is_dir=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    if is_dir:
        p.mkdir(exist_ok=True)
    else:
        p.write_text("stub")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_GRYPE_DIR", str(tmp_path / "grype-db"))
    monkeypatch.setenv("QUANT_TMP_DIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir(exist_ok=True)


def test_grype_orphan_cleaned_live_kept(tmp_path, monkeypatch):
    import src.scheduler.tasks as T
    _env(monkeypatch, tmp_path)
    gd = Path(os.environ["QUANT_GRYPE_DIR"])
    orphan = _mk(gd / "grype-db-download123", 30 * 3600, is_dir=True)
    live = _mk(gd / "grype-db-download456", 3600, is_dir=True)
    keep = _mk(gd / "6", 40 * 3600, is_dir=True)   # 在用库版本目录——非 download* 前缀不动
    T._security_tmp_sweep()
    assert not orphan.exists()
    assert live.exists()
    assert keep.exists()


def test_sbom_old_deleted(tmp_path, monkeypatch):
    import src.scheduler.tasks as T
    _env(monkeypatch, tmp_path)
    old = _mk(Path(os.environ["QUANT_TMP_DIR"]) / "quant-sbom.json", 30 * 3600)
    T._security_tmp_sweep()
    assert not old.exists()


def test_old_lock_7d_boundary(tmp_path, monkeypatch):
    import src.scheduler.tasks as T
    _env(monkeypatch, tmp_path)
    old = _mk(Path(os.environ["QUANT_TMP_DIR"]) / "quant-req-20261020-x.lock", 8 * 86400)
    fresh = _mk(Path(os.environ["QUANT_TMP_DIR"]) / "quant-req-20261031-y.lock", 3600)
    T._security_tmp_sweep()
    assert not old.exists() and fresh.exists()
