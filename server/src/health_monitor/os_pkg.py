# -*- coding: utf-8 -*-
"""OS 包管理器抽象 + pip 枚举/上游（批 155 · 方案 §4.1/§4.2）。

detect_provider() 命令存在性优先；Unsupported 显式降级（B8 根除）。
全部结构化输出（rpm -q --qf / dpkg-query -W -f），禁人类文本解析。
上游源决策（2026-10-28 实测定谳）：pip 上游=pypi.org simple（PEP 691，
0.4s/包、权威、files[].yanked 原生）；tuna 作降级。tuna 镜像 JSON API
严重陈旧（certifi 停在 2024.7）禁用；tuna simple 同步延迟约一季。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("health_monitor")

PYPI_SIMPLE = "https://pypi.org/simple"
PYPI_SIMPLE_FALLBACK = "https://pypi.tuna.tsinghua.edu.cn/simple"
PYPI_SIMPLE_ENV = "PYPI_SIMPLE"


@dataclass(frozen=True)
class InstalledPkg:
    name: str
    version: str
    release: str = ""
    epoch: str = ""


def _run(cmd, timeout: int = 60) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out.check_returncode()
    return out.stdout or ""


class RpmDnf:
    """RHEL 系——rpm -qa 全量 + repoquery --upgrades。"""

    id = "dnf"

    def list_installed(self):
        raw = _run(["rpm", "-qa", "--qf", "%{NAME}\t%{EPOCH}\t%{VERSION}\t%{RELEASE}\n"],
                   timeout=120)
        pkgs = []
        for ln in raw.splitlines():
            p = ln.split("\t")
            if len(p) == 4:
                pkgs.append(InstalledPkg(
                    name=p[0], epoch="" if p[1] in ("(none)", "") else p[1],
                    version=p[2], release=p[3]))
        return pkgs

    def upgrade_candidates(self):
        """{包名: 仓库最高 evr 串}——repoquery --upgrades 一次全量（enabled repos）。"""
        raw = _run(["dnf", "-q", "repoquery", "--upgrades", "--qf",
                    "%{NAME}\t%{EPOCH}\t%{VERSION}\t%{RELEASE}\n"], timeout=180)
        out = {}
        for ln in raw.splitlines():
            p = ln.split("\t")
            if len(p) == 4 and p[0]:
                out[p[0]] = p[2] + "-" + p[3]
        return out


class DpkgApt:
    """Debian 系——dpkg-query 结构化（apt-get -s 文本禁解析，审A-H6）。"""

    id = "apt"

    def list_installed(self):
        raw = _run(["dpkg-query", "-W", "-f", "${Package}\t${Version}\n"], timeout=120)
        pkgs = []
        for ln in raw.splitlines():
            p = ln.split("\t")
            if len(p) == 2 and p[0] and not p[0].startswith("rc:"):
                pkgs.append(InstalledPkg(name=p[0], version=p[1]))
        return pkgs

    def upgrade_candidates(self):
        return {}      # 无结构化通道：latest 缺 -> unknown（不猜）


class Unsupported:
    """无适配包管理器——显式空集+原因（采集侧必须 degraded，B8）。"""

    id = "unsupported"
    reason = "当前发行版无包管理器适配（支持：dnf 系 / apt 系）"

    def list_installed(self):
        return []

    def upgrade_candidates(self):
        return {}


def detect_provider():
    """命令存在性优先（审B-L1）；都不在 -> Unsupported（显式）。"""
    import shutil
    try:
        if shutil.which("rpm") and shutil.which("dnf"):
            return RpmDnf()
        if shutil.which("dpkg-query") and shutil.which("apt-get"):
            return DpkgApt()
    except Exception:
        pass
    return Unsupported()

# ── pip 面 ────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def pip_installed() -> list:
    """importlib 全枚举 -> [{name, cur, local}]（local=direct_url.json，PEP 610）。"""
    import importlib.metadata as md
    out = []
    for dist in md.distributions():
        name = dist.metadata["Name"] or ""
        if not name:
            continue
        try:
            local = bool(dist.read_text("direct_url.json"))
        except Exception:
            local = False
        out.append({"name": _norm(name), "cur": dist.version, "local": local})
    return out


def pypi_versions(name: str, base=None, timeout: int = 10) -> dict:
    """PEP 691 simple -> {"versions": 非yanked[], "yanked": []}。

    版本提取自文件名；同版本任一文件非 yank 即可用；失败抛异常由调用方
    按包记 error（单包失败不炸整体）。
    """
    import urllib.request
    origin = base or os.environ.get(PYPI_SIMPLE_ENV) or PYPI_SIMPLE
    req = urllib.request.Request(f"{origin}/{name}/",
                                 headers={"Accept": "application/vnd.pypi.simple.v1+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    ver_yanked = {}
    for f in payload.get("files", []):
        m = re.match(
            r"^[A-Za-z0-9_.]+?-([A-Za-z0-9.!+*]+?)"
            r"(?:-py\d|-cp\d|-none-any|\.whl|\.tar|\.zip)",
            f.get("filename", ""))
        if not m:
            continue
        v = m.group(1)
        yk = bool(f.get("yanked"))
        if v not in ver_yanked:
            ver_yanked[v] = yk
        elif not yk:
            ver_yanked[v] = False
    return {"versions": sorted(v for v, y in ver_yanked.items() if not y),
            "yanked": sorted(v for v, y in ver_yanked.items() if y)}
