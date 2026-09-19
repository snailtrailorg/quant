"""权限解析（批11C 下沉自 web_api/auth.py——服务层合法消费,IM 身份链同源）。

批33a（2026-09-17）：权限单源化——user 维退役（用户裁定：权限完全追随组；生产
permission 表 user 行复核=0；迁移 0082 CHECK 锁值域仅 role）。语义与锁键防线原样：
PERMISSIONS 字典=DB 故障回退底座；表有行全量以表为准；锁键/地板见各常量。
web_api/auth.py re-export 保持全部既有引用路径不变（函数内 import 每次解析,patch 兼容）。
"""
from __future__ import annotations

import logging

from src.data_platform.db import get_conn   # noqa: F401（部分函数内再 import as _gc 同源）

_logger = logging.getLogger("data_platform.perms")


# 批33b（A-P1-4）：唯一字面量层=perm_registry（api 键 14/nav/market 全在那）；本模块
# 单向 import——admin 集=注册表 api 全键派生（三集实测相等），其余角色子集保留字面量（角色语义独立）。
from src.data_platform.perm_registry import API_PERM_KEYS as _API_KEYS, MARKET_OP_KEYS as _MKT_KEYS

PERMISSIONS = {
    "viewer":  {"read"},
    "analyst": {"read", "strategy_control", "data_sync"},   # 研究：策略/回测/数据同步。
    # W5 修 P0（盲审 B）：system_config 原为死键（迁移前 0 端点消费）——W5 迁移把 16 个
    # admin-only 门（数据源/通道/券商凭证 CRUD/system-config 写/health）挂上后 analyst
    # 经回退字典全部可达=扩权回归。删键归位 admin-only（wd-10"收紧 analyst"方向）。
    "trader":  {"read", "strategy_control", "halt", "trade", "live_trading_control"},  # 交易：策略启停/熔断/下单/实盘开关
    "admin":   set(_API_KEYS),   # 批33b：注册表派生（admin 专属全集；analyst 无）
}

_PERM_CACHE: dict = {"at": 0.0, "roles": None}
_PERM_TTL = 60.0

# W4（盲审 B-P0 新建——原"系统策略键已锁定"是幻觉：require_perm 全表驱动,任何键今天都可被
# 角色重写关掉）：锁键=提权链/自损链高危键。批33a：user override 双路径已随 user 维退役
# （组层单锁）；admin 角色重写地板键（self-lockout 防线）保留组层。
LOCKED_PERM_KEYS = {"user_mgmt", "resume"}   # 批55b:account_keys 退役(收编外部接口管理)
ADMIN_ROLE_FLOOR = LOCKED_PERM_KEYS | {"system_config", "alerts_config"}   # 批7:告警路由/计费短信面同列自锁防线

# 批15：市场操作权限（market_op 维）——市场键与实盘分项开关同键（risk._market_of 返回集）。
# 批33b：字面量层迁 perm_registry（本常量改派生 re-export——全部既有引用零改动，盲审 P1-5/P2-7 单源延续）。
_MARKET_OP_KEYS = _MKT_KEYS


def market_op_allowed(username: str, role: str, market: str) -> bool:
    """批15 market_op 维：role 行(effect)；无行 False。

    批33a：user 层退役——单层 role 判定（username 参数保留=调用方签名兼容，不再参与查询）。

    全链 fail-closed（2026-09-11 用户裁定）：
    - 无行=False——组未配置即拒（新市场键漏配=默认锁死；自定义新组零行=天然零权限）
    - 同 (role,market) allow+deny 双行并存时 deny 优先（对齐 api 维 deny 语义）
    读库失败=False（对齐 check_order 链上件：熔断态/快照/开关均 fail-closed）
    - 不做缓存：直读 PG——invalidate_perm_cache 是进程内的，永远到不了 strategy_runner
      子进程（check_order 调用方），缓存=权限收紧对运行中任务永不生效的 fail-open 面
      （盲审 A-P1-4/B-P1-5 同判）；下单=信号级低频（max_trades_per_day 默认 20），直读可忽略。
    """
    from src.data_platform.db import get_conn as _gc
    try:
        with _gc() as conn:
            cur = conn.execute(
                "SELECT effect FROM permission "
                "WHERE dimension='market_op' AND resource=%s "
                "AND subject_type='role' AND subject_id=%s",
                (market, role))
            rows = cur.fetchall()
    except Exception as e:
        _logger.warning("market_op 表读取失败（fail-closed 拒）: %s", e)
        return False

    effs = {eff for (eff,) in rows}
    if "deny" in effs:
        return False
    return "allow" in effs


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
    _PERM_CACHE.update(at=0.0, roles=None)


def load_nav_map(username: str, role: str) -> dict:
    """W5：nav 维映射（resource=菜单id → hidden|readonly|readwrite）。

    批33a：user 覆盖层退役——纯 role 判定（username 参数保留=签名兼容）。
    批39 B-P2-3：nav 维幽灵行剥离（菜单改名/删除后 permission 表残留行——不滤则回显即
    回传，BAD_RESOURCE 卡死该组 nav 保存；读侧白名单=注册表 NAV 条目 id 集）。
    无配置={}（=readwrite 缺省，前端现行为）。
    """
    try:
        from src.data_platform.perm_registry import NAV_ITEMS_BASE
        from src.data_platform.db import get_conn as _gc
        with _gc() as conn:
            rows = conn.execute(
                "SELECT resource, effect FROM permission "
                "WHERE dimension='nav' AND subject_type='role' AND subject_id=%s", (role,)).fetchall()
        known = {e["id"] for e in NAV_ITEMS_BASE}
        out = {}
        for res, eff in rows:
            if res not in known:
                _logger.warning("nav 维幽灵行忽略（resource=%s 不在注册表——菜单改名/删除残留）", res)
                continue
            out[res] = eff
        return out
    except Exception:
        return {}


def load_effective_permissions(username: str, role: str) -> tuple[set, dict]:
    """W4 C 阶段（批33a 单源化）：用户有效权限 = 角色 base（user 维退役——用户裁定权限
    完全追随组，新组合=建新组）。

    返回 (perms, sources)：sources 供玻璃盒标注来源（api 维）——签名与返回形状保持
    （调用方/5 测试文件 patch 零改动）：批33a 后恒 {"<perm>": "role-base"} +
    {"__denied__": []}（user 维不存在，键保留=前端玻璃盒零逻辑改动）。
    """
    roles = load_role_permissions()
    base = set(roles.get(role, set()))
    sources = {p: "role-base" for p in base}
    sources["__denied__"] = []
    return base, sources
