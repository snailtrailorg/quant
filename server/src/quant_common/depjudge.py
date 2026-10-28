# -*- coding: utf-8 -*-
"""依赖健康判定引擎（批 155 · P0-1）。

纯函数层（零业务依赖、零 IO）——分层铁律居 quant_common；
health_monitor/os_pkg 依赖本模块，方向单向（judge 不 import os_pkg）。

语义真源：flow/方案/批155-依赖健康检测重构-设计.md §3（v2.4 定稿）。
七分类与规则序（drift 前置、local 恒先、比较先于 pinned、7b 用预算好的
in_constraint_max——判定零外部命令）。
"""

from __future__ import annotations

import re

DEP_AUDIT_FRESH_H = 30          # 缓存新鲜度线（小时）——全仓唯一定义（B1 根除）

HAS_UPDATE_YES = "yes"
HAS_UPDATE_NO = "no"
HAS_UPDATE_UNKNOWN = "unknown"

_CLASSES = ("actionable", "in_constraint", "pinned", "current",
            "drift", "unreachable", "unknown")


def _v(raw):
    """PEP440 解析；None/非法 -> None（行级不可比，禁异常外溢）。"""
    if raw is None:
        return None
    try:
        from packaging.version import Version
        return Version(str(raw).strip())
    except Exception:
        return None


def _in(ver, constraint) -> bool:
    """ver 满足约束串？空约束/非法约束恒 True（fail-open，宁漏报 pinned 不误报）。"""
    if not constraint:
        return True
    try:
        from packaging.specifiers import SpecifierSet
        return ver in SpecifierSet(str(constraint))
    except Exception:
        return True


def judge(cur: str | None, constraint: str | None, latest: str | None,
          in_constraint_max: str | None, requires_python_ok: bool,
          managed_by: str) -> tuple[str, str]:
    """单一判定入口 -> (judge_class, judge_why)。行级异常自捕获 -> unknown。"""
    try:
        if managed_by == "local":
            return "unreachable", "本地构建 fork，不与上游比较"
        if managed_by in ("", "none", None):
            return "unknown", "无版本坐标（非 pypi/rpm 纳管）"
        cur_v = _v(cur)
        if cur_v is None:
            return "unknown", f"当前版本不可解析: {cur!r}"
        if constraint and not _in(cur_v, constraint):
            return "drift", f"环境与 requirements 漂移：装 {cur}，约束 {constraint}"
        if not latest:
            return "unknown", "上游不可达或未登记"
        if requires_python_ok is False:
            return "unreachable", "上游要求更高 Python 版本"
        lat_v = _v(latest)
        if lat_v is None:
            return "unknown", f"上游版本不可解析: {latest!r}"
        if lat_v <= cur_v:
            return "current", ""
        if _in(lat_v, constraint):
            return "actionable", f"可升级 {cur} -> {latest}"
        if in_constraint_max:
            icm = _v(in_constraint_max)
            if icm is not None and icm > cur_v:
                return "in_constraint", (
                    f"约束内可升至 {in_constraint_max}；上游 {latest} 受界 {constraint} 挡")
        return "pinned", f"受上界 {constraint} 策略保持；上游 {latest} 不可达"
    except Exception as e:                       # 行级兜底（§5 矩阵：不炸轮）
        return "unknown", f"内部错误: {e}"


def has_update_of(judge_class: str | None) -> str:
    """judge_class -> yes/no/unknown（服务端单源，v2.1 裁定）。"""
    if judge_class in ("actionable", "in_constraint"):
        return HAS_UPDATE_YES
    if judge_class in _CLASSES:
        return HAS_UPDATE_NO
    return HAS_UPDATE_UNKNOWN                     # None/未判定/迁移存量


def _norm(name: str) -> str:
    """PEP 503 规范名（小写、-_. 归一）——与 importlib.metadata 键对齐。"""
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def norm(name: str) -> str:
    """公开规范名（采集/缓存/台账统一键）。"""
    return _norm(name)


def parse_constraints(text: str) -> dict[str, str]:
    """requirements 文本 -> {规范名: 约束串}（无约束行不入；非法行跳过 fail-soft）。"""
    out: dict[str, str] = {}
    if not text:
        return out
    try:
        from packaging.requirements import Requirement
    except Exception:
        return out
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-r ", "--")):
            continue
        try:
            req = Requirement(line)
            spec = str(req.specifier) or None
            if spec:
                out[_norm(req.name)] = spec
        except Exception:
            continue
    return out


def parse_names(text: str) -> list[str]:
    """requirements 文本 -> [规范名]（dev 名单；非法行跳过）。"""
    names: list[str] = []
    if not text:
        return names
    try:
        from packaging.requirements import Requirement
    except Exception:
        return names
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-r ", "--")):
            continue
        try:
            names.append(_norm(Requirement(line).name))
        except Exception:
            continue
    return names


def in_constraint_max(versions, constraint, cur) -> str | None:
    """版本表 -> 约束内 >cur 最高版（采集侧预算落缓存；yanked 已剔除）。"""
    if not versions or not constraint:
        return None
    cur_v = _v(cur) if cur else None
    best, best_v = None, None
    for raw in versions:
        v = _v(raw)
        if v is None or not _in(v, constraint):
            continue
        if cur_v is not None and v <= cur_v:
            continue
        if best_v is None or v > best_v:
            best, best_v = raw, v
    return best


def evr_key(epoch, version, release):
    """OS evr 比较键（简化形态——无 tilde 场景可直接排序比较）。

    **tilde 语义（1.0~rc1 < 1.0）无法用裸键表达**（前缀规则会判长>短）——
    含 tilde 的比较一律走 evr_compare()；本键用于无 tilde 的常规排序场景。
    """
    e = int(epoch) if str(epoch or "").isdigit() and str(epoch) != "" else 0
    return (e, _segs(version), _segs(release))


def evr_compare(a, b) -> int:
    """rpm evr 全语义比较（含 tilde）：返回 -1/0/1。

    a/b 形如 (epoch, version, release) 原始串三元组——tilde 段参与逐段比较，
    (-1,'~') 恒小于任何段；同位平局时长短决胜（长=新，tilde 段除外——由段值
    先行保证）。审B-M5 立法的完整实现。
    """
    ea = int(a[0]) if str(a[0] or "").isdigit() and str(a[0]) != "" else 0
    eb = int(b[0]) if str(b[0] or "").isdigit() and str(b[0]) != "" else 0
    if ea != eb:
        return 1 if ea > eb else -1

    def _cmp_part(pa: list, pb: list) -> int:
        for va, vb in zip(pa, pb):
            if va != vb:
                return -1 if va < vb else 1
        if len(pa) != len(pb):
            # tilde 前缀平局后一侧更长：含 tilde 尾段者恒小（1.0~rc1 < 1.0）
            a_t = (-1, "~") in pa[len(pb):]
            b_t = (-1, "~") in pb[len(pa):]
            if a_t and not b_t:
                return -1
            if b_t and not a_t:
                return 1
            return 1 if len(pa) > len(pb) else -1
        return 0

    r = _cmp_part(_segs(a[1]), _segs(b[1]))
    if r != 0:
        return r
    return _cmp_part(_segs(a[2]), _segs(b[2]))


def _segs(s) -> list[tuple]:
    """rpmver 段化：非字母数字=边界（'1.0'→(1,)(0,) 非 (10,)）；
    tilde 独立段 (-1,'~') 恒小；字母/数字交替子段（rc1→rc,1）。"""
    out: list[tuple] = []
    buf, isd = "", None
    for ch in str(s or ""):
        if ch == "~":
            if buf:
                out.append(_seg(buf, isd))
                buf, isd = "", None
            out.append((-1, "~"))
            continue
        if not ch.isalnum():
            if buf:
                out.append(_seg(buf, isd))
                buf, isd = "", None
            continue
        cur_d = ch.isdigit()
        if isd is None:
            buf, isd = ch, cur_d
        elif cur_d == isd:
            buf += ch
        else:
            out.append(_seg(buf, isd))
            buf, isd = ch, cur_d
    if buf:
        out.append(_seg(buf, isd))
    return out

def _seg(buf: str, is_digit: bool) -> tuple:
    if buf == "~":
        return (-1, "~")
    if is_digit:
        return (1, int(buf))
    return (0, buf.lower())
