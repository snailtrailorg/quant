"""权限解析（批11C 下沉自 web_api/auth.py——服务层合法消费,IM 身份链同源）。

语义与锁键防线原样：PERMISSIONS 字典=DB 故障回退底座；表有行全量以表为准；
user deny > user allow > role allow；锁键/地板见各常量。web_api/auth.py re-export
保持全部既有引用路径不变（函数内 import 每次解析,patch 兼容）。
"""
from __future__ import annotations

import logging

from src.data_platform.db import get_conn   # noqa: F401（部分函数内再 import as _gc 同源）

_logger = logging.getLogger("data_platform.perms")


PERMISSIONS = {
    "viewer":  {"read"},
    "analyst": {"read", "strategy_control", "data_sync"},   # 研究：策略/回测/数据同步。
    # W5 修 P0（盲审 B）：system_config 原为死键（迁移前 0 端点消费）——W5 迁移把 16 个
    # admin-only 门（数据源/通道/券商凭证 CRUD/system-config 写/health）挂上后 analyst
    # 经回退字典全部可达=扩权回归。删键归位 admin-only（wd-10"收紧 analyst"方向）。
    "trader":  {"read", "strategy_control", "halt", "trade", "live_trading_control"},  # 交易：策略启停/熔断/下单/实盘开关
    "admin":   {"read", "strategy_control", "data_sync", "halt", "resume", "trade", "live_trading_control",
                 "risk_rules", "account_keys", "user_mgmt", "system_config", "llm_config", "im_bots_config",
                 "alerts_config"},   # 批7:DB 故障 fallback 亦含(admin 专属;analyst 无)
}

_PERM_CACHE: dict = {"at": 0.0, "roles": None, "users": {}}
_PERM_TTL = 60.0

# W4（盲审 B-P0 新建——原"系统策略键已锁定"是幻觉：require_perm 全表驱动,任何键今天都可被
# 角色重写关掉）：锁键=提权链/自损链高危键——角色重写与 user override 双路径同锁;
# admin 角色重写另有地板键（self-lockout 防线）
LOCKED_PERM_KEYS = {"user_mgmt", "resume", "account_keys"}
ADMIN_ROLE_FLOOR = LOCKED_PERM_KEYS | {"system_config", "alerts_config"}   # 批7:告警路由/计费短信面同列自锁防线


def load_role_permissions() -> dict:
    """角色→权限集（api 维）。表读失败/空 → fallback 字典（行为零变化）。"""
    import time as _t
    now = _t.time()
    if _PERM_CACHE["roles"] is not None and now - _PERM_CACHE["at"] < _PERM_TTL:
        return _PERM_CACHE["roles"]
    try:
        from src.data_platform.db import get_conn as _gc
        with _gc() as conn:
            cur = conn.execute(
                "SELECT subject_id, resource, effect FROM permission "
                "WHERE subject_type='role' AND dimension='api'")
            roles: dict = {}
            for sid, res, eff in cur.fetchall():
                # W4 修沉疴：原 `grants, denies = setdefault(...)` 解包的是 dict 的键
                # （'allow'/'deny' 字符串）非值——表有行即 AttributeError 静默回退字典，
                # 管理页保存过的权限从未生效（产线表恒空所以从未暴露;W4 基线测试首触发）
                gd = roles.setdefault(sid, {"allow": set(), "deny": set()})
                (gd["deny"] if eff == "deny" else gd["allow"]).add(res)
        merged = {}
        for role, base in PERMISSIONS.items():
            tab = roles.get(role, {})
            # 终审 P1-4 修正：表有该 role 的 allow 行 → 全量以表为准（撤权/全量重写生效）；
            # 表无行（未管理过的角色）→ 字典兜底。deny 行始终从结果里减（10 §1 deny 优先）。
            # W4 修沉疴②：无表行的角色 tab={} → tab["allow"] KeyError 同被空表掩盖
            tab_allow = tab.get("allow") or set()
            allow = (tab_allow if tab_allow else base) - tab.get("deny", set())
            merged[role] = allow
        for role, gd in roles.items():
            if role not in PERMISSIONS:
                merged[role] = gd["allow"] - gd["deny"]
        _PERM_CACHE.update(at=now, roles=merged)
        return merged
    except Exception as e:
        _logger.warning("permission 表读取失败（回退字典）: %s", e)
        return PERMISSIONS


def invalidate_perm_cache() -> None:
    """权限变更后即刻生效（10 §3：指纹重编译）。全局清（W4 保持现语义——单 worker
    写后即生效,勿改按键清留 role 脏键,盲审 B-P1）。"""
    _PERM_CACHE.update(at=0.0, roles=None, users={})


def _load_user_api_overrides(username: str) -> tuple[set, set]:
    """user 维 api override →（allows, denies）。读失败 raise 由调用方决定 fail-open。"""
    from src.data_platform.db import get_conn as _gc
    with _gc() as conn:
        cur = conn.execute(
            "SELECT resource, effect FROM permission "
            "WHERE subject_type='user' AND subject_id=%s AND dimension='api'", (username,))
        allows, denies = set(), set()
        for res, eff in cur.fetchall():
            (denies if eff == "deny" else allows).add(res)
    return allows, denies


def data_sensitivity(username: str, role: str) -> str:
    """W5：data 维敏感级（detail|aggregated|count，缺省 detail=现行为零变化）。

    解析 resource='sensitivity:<v>' 行（W4 Permissions.vue 编码）。user 行覆盖
    role 行（单值字段的 deny 语义=用户级值生效）；无任何配置=detail。读失败=detail。
    """
    try:
        from src.data_platform.db import get_conn as _gc
        with _gc() as conn:
            rows = conn.execute(
                "SELECT subject_type, subject_id, resource FROM permission "
                "WHERE dimension='data' AND resource LIKE 'sensitivity:%'").fetchall()
        role_v = user_v = None
        for st, sid, res in rows:
            v = res.split(":", 1)[1]
            if st == "role" and sid == role:
                role_v = v
            elif st == "user" and sid == username:
                user_v = v
        return user_v or role_v or "detail"
    except Exception:
        return "detail"


def load_nav_map(username: str, role: str) -> dict:
    """W5：nav 维三态映射（resource=菜单id → hidden|readonly|readwrite）。

    user 行覆盖 role 行（与 data 维同规则）；无配置={}（=readwrite 缺省，前端现行为）。
    """
    try:
        from src.data_platform.db import get_conn as _gc
        with _gc() as conn:
            rows = conn.execute(
                "SELECT subject_type, subject_id, resource, effect FROM permission "
                "WHERE dimension='nav'").fetchall()
        out: dict = {}
        for st, sid, res, eff in rows:
            if st == "role" and sid == role:
                out[res] = eff
        for st, sid, res, eff in rows:
            if st == "user" and sid == username:
                out[res] = eff
        return out
    except Exception:
        return {}


def load_effective_permissions(username: str, role: str) -> tuple[set, dict]:
    """W4 C 阶段：用户有效权限 = user deny > user allow > role allow（10 §3 合并序）。

    返回 (perms, sources)：sources 供玻璃盒标注来源（api 维）——
    {"<perm>": "user-override" | "role-base"} + {"__denied__": [被 user deny 的 role 键]}。
    user 维读失败 fail-open=按角色（user 维无字典可回,盲审 A-P1）。
    """
    roles = load_role_permissions()
    base = set(roles.get(role, set()))
    if not username:
        return base, {p: "role-base" for p in base}
    key = (username, role)
    cached = _PERM_CACHE["users"].get(key)
    if cached is None:
        try:
            cached = _load_user_api_overrides(username)
            _PERM_CACHE["users"][key] = cached
        except Exception:
            # 盲审 A-P1b：失败结果**不缓存**（users 槽无 TTL,缓存=一次 DB 抖动把该用户
            # fail-open 冻结到下次 invalidate）——本次按角色返回,下次重试
            cached = (set(), set())
    allows, denies = cached
    denied = sorted(base & denies)
    perms = (base | allows) - denies
    sources = {p: ("user-override" if (p in allows and p not in base) else "role-base")
               for p in perms}
    sources["__denied__"] = denied
    return perms, sources
