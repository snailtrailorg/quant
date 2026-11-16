# -*- coding: utf-8 -*-
"""OS 包管理器抽象 + pip 枚举/上游（批 155 · 方案 §4.1/§4.2）。

detect_provider() 命令存在性优先；Unsupported 显式降级（B8 根除）。
全部结构化输出（rpm -q --qf / dpkg-query -W -f），禁人类文本解析。

**provider 契约（三个方法，实现可缺席但接口必须在）**：
- `id: str`                       —— 实现标识（dnf / apt / unsupported）
- `list_installed() -> [InstalledPkg]`
- `upgrade_candidates() -> {name: evr}`  —— 空 dict = 无结构化通道 ⇒ 判 unknown（不猜）
- `repo_packages() -> set[str]`         —— 仓库中存在的包名（区分 current / unknown）
apt 的 upgrade/repo 两项**按 2026-11-16 用户裁定只保留接口**（返回空集，判定诚实降级）；
换发行版=新增 provider 实现，域（source='os'）与判定契约都不动。
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
PYPI_CACHE_TTL_D = 3          # 上游版本缓存天数（批181：7d→3d；漏洞包由 dep_audit 传更短 TTL）


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

    def repo_packages(self) -> set:
        """仓库中存在（任一 enabled repo）的包名全集——批185：判定「已最新 current」
        与「仓库无坐标 unknown」的分界（缺它则所有无升级包都落 unknown）。"""
        raw = _run(["dnf", "-q", "repoquery", "--qf", "%{NAME}\n"], timeout=240)
        return {ln.strip() for ln in raw.splitlines() if ln.strip()}


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
        # 无结构化通道：latest 缺 -> unknown（不猜）。用户裁定（2026-11-16）：
        # **apt 只保留接口**，不实现——接口在、判定诚实降级即可。
        return {}

    def repo_packages(self) -> set:
        """同 upgrade_candidates：接口保留、不实现 ⇒ 空集 ⇒ 判定落 unknown（诚实）。"""
        return set()


class Unsupported:
    """无适配包管理器——显式空集+原因（采集侧必须 degraded，B8）。"""

    id = "unsupported"
    reason = "当前发行版无包管理器适配（支持：dnf 系 / apt 系）"

    def list_installed(self):
        return []

    def upgrade_candidates(self):
        return {}

    def repo_packages(self) -> set:
        return set()


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


def pypi_versions_cached(name: str, timeout: int = 10,
                         ttl_d: int = PYPI_CACHE_TTL_D) -> dict:
    """缓存层：Valkey sec:pypi:<name>（TTL=PYPI_CACHE_TTL_D，批181 由 7d 缩为 3d；
    批 161：恒读缓存，到期自然重查=天然轮转；续跑判据在表侧（last_seen），缓存仅跨轮加速）。
    Valkey 不可达时静默回退直查（缓存是加速器非依赖）。

    批 179b：上游刷新只在 dep_audit 慢路径（异步 + 心跳续跑），本函数恒读缓存；
    扫描读侧不改这里、不绕过缓存，零联网。
    """
    try:
        import json as _json
        from src.quant_common.redis_client import business_redis
        key = f"sec:pypi:{name}"
        br = business_redis(socket_timeout=3, socket_connect_timeout=2)   # 批182：单次 IO 上限
        hit = br.get(key)
        if hit:
            return _json.loads(hit)
        result = pypi_versions(name, timeout=timeout)
        try:
            br.set(key, _json.dumps(result), ex=ttl_d * 86400)
        except Exception:
            pass
        return result
    except Exception:
        return pypi_versions(name, timeout=timeout)


def pypi_versions_refresh(name: str, timeout: int = 10, ttl_d: int = 1) -> dict:
    """慢路径主动刷新（批181）：仅 dep_audit 的优先集合调用——直查上游并按短 TTL 回写。
    与 pypi_versions_cached 的分工：后者恒读缓存（读侧/普通包），本函数是「写侧刷新」，
    不得在 security_scan 等读路径调用。
    """
    result = pypi_versions(name, timeout=timeout)
    try:
        import json as _json
        from src.quant_common.redis_client import business_redis
        business_redis(socket_timeout=3, socket_connect_timeout=2).set(
            f"sec:pypi:{name}", _json.dumps(result), ex=ttl_d * 86400)
    except Exception:
        pass
    return result


def pypi_versions(name: str, base=None, timeout: int = 10) -> dict:
    """PEP 691 simple -> {"versions": 非yanked[], "yanked": []}。

    版本提取自文件名；同版本任一文件非 yank 即可用；失败抛异常由调用方
    按包记 error（单包失败不炸整体）。
    """
    import urllib.request
    origin = base or os.environ.get(PYPI_SIMPLE_ENV) or PYPI_SIMPLE
    # 批182b：gzip——pyzmq 首页 3.0MB/124s → 506KB/6.6s（19x）。timeout 只限单次
    # recv，拦不住大响应慢速传输；不压缩时 175 个 miss 包要数小时。
    req = urllib.request.Request(
        f"{origin}/{name}/",
        headers={"Accept": "application/vnd.pypi.simple.v1+json",
                 "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            import gzip as _gz
            raw = _gz.decompress(raw)
    payload = json.loads(raw)
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

# ── 外部工具探测（批185：从 scanners.py 并入——同属「外部能力是否在位」）─────────────
# 说明：**这不是注册表**。注册表在本仓指 `src/registry/`（D32 的 (domain, provider)
# 插件注册，带 manifest/port_caps）。此处只做「命令存在性探测」，消费者仅安全采集
# （security_collector），不进 health_monitor 的规则链（那条链管服务/资源，响应是重启；
# 扫描器缺失的响应是装二进制/查 DB/重扫，属安全域，告警走 security.stale）。
import shutil as _shutil

_TOOL_REQUIRED = ("grype", "syft")


class Scanner:
    """扫描器能力接口：id / missing() / status()。"""

    id = "none"

    def missing(self) -> list[str]:
        return list(_TOOL_REQUIRED)

    def status(self) -> dict:
        miss = self.missing()
        return {"id": self.id, "available": not miss,
                "reason": "" if not miss else "缺少二进制: " + ", ".join(miss)}


class GrypeScanner(Scanner):
    """grype + syft 组合（SBOM 生成 + CVE 匹配），批 123 单源终裁。"""

    id = "grype"

    def missing(self) -> list[str]:
        return [b for b in _TOOL_REQUIRED if not _shutil.which(b)]


def detect_scanner() -> Scanner:
    """唯一探测入口（命令存在性优先）。返回实例不代表可用——读 status()。"""
    return GrypeScanner()
