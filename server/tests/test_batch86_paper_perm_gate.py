"""批 86-B · 纸上交易权限生效门：**表路径**下的 api/nav 两维逐格断言（批 76b 闸门的姊妹篇）。

## 它钉什么（与 test_batch76b_perm_effective_gate.py 的分工）

batch76b 闸门钉的是**覆盖性**（每个被引用的键 ≥1 角色持有）。本文件钉批 86-B 的
**逐格矩阵**——`flow/任务/批86-实盘测试与操作列整理.md` §3.2 已裁定矩阵的表路径形态：

| 维度 | 资源 | analyst | trader | admin | viewer |
|------|------|---------|--------|-------|--------|
| api  | `paper_trade`  | allow | allow | allow | **无** |
| nav  | `paper-trade`  | readwrite | readwrite | readwrite | **hidden** |
| nav  | `live-task`    | **hidden** | readwrite(缺省) | readwrite(缺省) | **hidden** |

三个格子是**本批新立据**，缺一即病：
- nav `live-task` hidden × analyst：治理原则本身（造策略的人≠验策略的人，
  不透明是刻意的——防「为通过而调参」）；只有 api 门没有 nav 行，analyst 菜单照样出现。
- nav `paper-trade` hidden × viewer：nav 无行=缺省 readwrite（`load_nav_map` 无字典兜底），
  漏行=「nav 声称 viewer 可用、API 403」的**撕裂态**。
- api `paper_trade` 无 viewer 行：只读观察者不给纸上交易能力（§五 矩阵裁定）。

## 为什么「反证组」是本文件的一半价值

顺序断言（batch76b 的 TestGateCatchesRegression 同法）：先证**迁移前的形状会红**，
再证迁移后绿——否则断言无法区分「迁移生效了」和「断言本来就恒真」。
"""
from unittest.mock import MagicMock, patch

import pytest

import src.data_platform.perms as perms_mod


# ——— prod 形状 permission 表（= batch76b 同一份 fixture 的行，含 0124 live_control）———
def _api_rows() -> list[tuple[str, str, str]]:
    """(role, resource, effect) 三元组——api 维 prod 形状 + 0124。"""
    return [
        ("viewer", "read", "allow"),
        ("analyst", "read", "allow"),
        ("analyst", "strategy_control", "allow"),
        ("analyst", "data_sync", "allow"),
        ("trader", "read", "allow"),
        ("trader", "strategy_control", "allow"),
        ("trader", "halt", "allow"),
        ("trader", "trade", "allow"),
        ("trader", "live_trading_control", "allow"),
        ("admin", "read", "allow"),
        ("admin", "strategy_control", "allow"),
        ("admin", "data_sync", "allow"),
        ("admin", "halt", "allow"),
        ("admin", "resume", "allow"),
        ("admin", "trade", "allow"),
        ("admin", "live_trading_control", "allow"),
        ("admin", "risk_rules", "allow"),
        ("admin", "user_mgmt", "allow"),
        ("admin", "system_config", "allow"),
        ("admin", "llm_config", "allow"),
        ("admin", "im_bots_config", "allow"),
        ("admin", "alerts_config", "allow"),
        ("admin", "live_control", "allow"),   # 0124
        ("trader", "live_control", "allow"),  # 0124
    ]


# 迁移 0126 补种（批 86-B）——**本文件的被测对象**。⚠️ 原拟含 strategy_pretest，
# 落码时被覆盖性闸门证伪为死键而撤销（详见 0126 注释与 perm_registry 注）。
_MIGRATION_0126_API = [
    ("analyst", "paper_trade", "allow"),
    ("trader", "paper_trade", "allow"),
    ("admin", "paper_trade", "allow"),
]
_MIGRATION_0126_NAV = [
    # (role, resource, effect)
    ("analyst", "paper-trade", "readwrite"),
    ("trader", "paper-trade", "readwrite"),
    ("admin", "paper-trade", "readwrite"),
    ("viewer", "paper-trade", "hidden"),
    ("analyst", "live-task", "hidden"),
    ("viewer", "live-task", "hidden"),
]


def _resolve_api(rows):
    """真 `load_role_permissions` 打桩解析 → {role: perm_set}。

    ⚠️ **必须还原 `_PERM_CACHE`**（batch76b `_resolve` 同款注释，修过一次真缺陷）：
    `load_role_permissions` 解析成功会写 60s 进程级缓存，留着它=后续自行打桩 DB 的
    测试读到本 fixture 而非自己的 mock（单独跑绿、同跑红的典型跨文件污染）。
    """
    saved = dict(perms_mod._PERM_CACHE)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = rows
    perms_mod._PERM_CACHE.update(at=0.0, roles=None)
    try:
        with patch("src.data_platform.db.get_conn", return_value=conn):
            return perms_mod.load_role_permissions()
    finally:
        perms_mod._PERM_CACHE.update(saved)


def _resolve_nav(role_rows):
    """真 `load_nav_map` 打桩解析 → {resource: effect}。

    `load_nav_map` 现无进程缓存（直读 DB），但同法 try/finally 归还 `_REGISTRY_CACHE`——
    防它将来加缓存时本文件的测试变成污染源（与 _resolve_api 同一个教训的预防性偿还）。
    `load_registry` 确有 `_REGISTRY_CACHE`，凡同进程先跑过注册表相关路径的测试都受其影响。
    """
    saved = dict(perms_mod._REGISTRY_CACHE) if hasattr(perms_mod, "_REGISTRY_CACHE") else None
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [(r, e) for (_role, r, e) in role_rows]
    try:
        with patch("src.data_platform.db.get_conn", return_value=conn):
            return perms_mod.load_nav_map("someone", "role-of-rows")
    finally:
        if saved is not None:
            perms_mod._REGISTRY_CACHE.update(saved)


def _post_0126_api():
    return _api_rows() + _MIGRATION_0126_API


# ══════════════════ ① api 维：paper_trade 的持有矩阵 ══════════════════

class TestPaperTradeApiMatrix:
    def test_three_roles_hold_paper_trade(self):
        roles = _resolve_api(_post_0126_api())
        for role in ("analyst", "trader", "admin"):
            assert "paper_trade" in roles[role], f"{role} 无 paper_trade ⇒ 纸上交易面对其全 403"

    def test_viewer_does_not_hold_paper_trade(self):
        roles = _resolve_api(_post_0126_api())
        assert "paper_trade" not in roles["viewer"], \
            "viewer 是只读观察者，不给纸上交易能力（§五 矩阵裁定）"

    def test_analyst_still_excluded_from_live_control(self):
        """前测职能 ≠ 实盘职能：paper_trade 的补种**不得**顺带把 analyst 提进实盘面。"""
        roles = _resolve_api(_post_0126_api())
        assert "live_control" not in roles["analyst"], \
            "补种 paper_trade 不得连带 live_control（批 77 §一 原则仍有效）"


# ══════════════════ ② nav 维：两资源的逐格矩阵 ══════════════════

class TestNavMatrix:
    def test_live_task_hidden_for_analyst_and_viewer(self):
        """本批新立据①：live-task 对 analyst=hidden（治理原则）+ viewer=hidden（矩阵裁定）。"""
        nav = _resolve_nav(_MIGRATION_0126_NAV)
        assert nav.get("live-task") == "hidden"

    def test_paper_trade_readwrite_for_three_roles(self):
        # load_nav_map 按 role 查询——三角色各解析一次（stub 喂同一行集的 role 维由调用方滤，
        # 这里喂的是全量行集，函数内按 subject_id 过滤由 SQL 完成；stub 直喂该角色的行）
        for role in ("analyst", "trader", "admin"):
            rows = [r for r in _MIGRATION_0126_NAV if r[0] == role]
            assert _resolve_nav(rows).get("paper-trade") == "readwrite", f"{role} 应 readwrite"

    def test_paper_trade_hidden_for_viewer(self):
        """本批新立据②：viewer 显式 hidden——**不是**依赖缺省。

        反面教材：viewer 无行 = nav 维缺省 readwrite = 「声称可用、API 403」撕裂态。
        这条断言若红（viewer 无 hidden 行），撕裂态即回归。
        """
        rows = [r for r in _MIGRATION_0126_NAV if r[0] == "viewer"]
        assert _resolve_nav(rows).get("paper-trade") == "hidden"

    def test_live_task_readwrite_default_for_trader_admin(self):
        """trader/admin 对 live-task 无行（缺省 readwrite）——钉住「不误伤既有可用性」。

        若有人「顺手」给 trader/admin 也补 hidden 行，实盘面菜单消失=功能回退（本断言即红）。
        """
        for role in ("trader", "admin"):
            rows = [r for r in _MIGRATION_0126_NAV if r[0] == role]
            nav = _resolve_nav(rows)
            assert "live-task" not in nav, \
                f"{role} 不应有 live-task nav 行（缺省 readwrite 是正确形态，别补行）"


# ══════════════════ ③ 反证组：迁移前的形状必须会红 ══════════════════

class TestGateCatchesRegression:
    def test_before_0126_nobody_holds_paper_trade(self):
        """0124 形状（迁移前）：paper_trade 无人持有 ⇒ 纸上交易面是 403 死面。

        这就是批 77 的 8 端点形态——断言「迁移是 load-bearing」而非「断言恒真」。
        """
        roles = _resolve_api(_api_rows())
        holders = [r for r, perms in roles.items() if "paper_trade" in perms]
        assert holders == [], f"0124 形状下不应有人持有 paper_trade，实得 {holders}"

    def test_before_0126_nav_has_no_rows(self):
        """迁移前 nav 行集为空 ⇒ analyst 的 live-task 走缺省 readwrite（= 治理原则未落地）。"""
        assert _resolve_nav([]) == {}, "0126 前 nav 行集应为空（缺省 readwrite）"


# ══════════════════ ④ mode 分档闸的 fail-closed 语义 ══════════════════

class TestTaskModePermGate:
    """`assert_task_perm`（auth.py）：按 live_task.mode 二选一判权 + **未知 mode 按最严**。

    这是多键形态（require_perm_any 过粗筛）后的第二道、**模式相关**的门——
    防 analyst 拿 paper_trade 过粗筛后操作 live 任务（或反向）。
    """

    def _call(self, mode, have):
        from src.web_api.auth import assert_task_perm
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (mode,)
        payload = {"username": "u", "db_role": "trader"}
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch("src.web_api.auth.load_effective_permissions", return_value=(have, None)):
            return assert_task_perm(payload, 5, action="测试")

    def test_paper_task_needs_paper_trade(self):
        assert self._call("paper", {"read", "paper_trade"}) == "paper_trade"

    def test_paper_task_denied_without_paper_trade(self):
        from src.web_api.errors import ApiError
        with pytest.raises(ApiError) as ei:
            self._call("paper", {"read", "live_control"})
        assert ei.value.code == "PERM_DENIED"

    def test_live_task_needs_live_control(self):
        assert self._call("live", {"read", "live_control"}) == "live_control"

    def test_unknown_mode_fails_closed_to_live_control(self):
        """mode 值非法（0125 CHECK 应拦，但闸不赌上游）：fail-closed 按 live_control 判。

        持 paper_trade 不持 live_control 的角色（analyst）对未知 mode 必须 403——
        宁可错杀，不可把一个身份不明的任务放进任何人的操作面。
        """
        from src.web_api.errors import ApiError
        with pytest.raises(ApiError) as ei:
            self._call("weird", {"read", "paper_trade"})
        assert ei.value.code == "PERM_DENIED"

    def test_missing_task_is_404(self):
        from src.web_api.errors import ApiError
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = None
        payload = {"username": "u", "db_role": "admin"}
        with patch("src.data_platform.db.get_conn", return_value=conn):
            with pytest.raises(ApiError) as ei:
                from src.web_api.auth import assert_task_perm
                assert_task_perm(payload, 999)
        assert ei.value.status_code == 404 and ei.value.code == "TASK_NOT_FOUND"

    def test_mapping_matches_migration_value_domain(self):
        """TASK_MODE_PERM 的键集必须与 0125 CHECK 值域 {'live','paper'} 严格一致。

        迁移加值（如未来的 'replay'）而映射没跟 ⇒ 新 mode 全部落 fail-closed 分支
        （行为是最严档，不算洞）但**映射漂移没人知道**——钉住让漂移必须显式过这关。
        """
        from src.web_api.auth import TASK_MODE_PERM
        assert set(TASK_MODE_PERM) == {"live", "paper"}
        assert TASK_MODE_PERM["paper"] == "paper_trade"
        assert TASK_MODE_PERM["live"] == "live_control"
