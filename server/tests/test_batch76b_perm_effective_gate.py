"""批 76b · P0 闸门：**DB 生效权限集**必须含全部源码声明的 perm 键（缺键=端点 403 死态）。

## 为什么需要这个闸门（它补的是批 77 的盲区）

批 77 拆出 `live_control` 后建了 `TestToolGatingContract` 钉「三面同键」——但它**只扫源码
字面量**：`require_perm("live_control")` 确实写在 `trading.py`/`strategy.py` 里，测试就绿了。
**它断言的是「代码声明一致」，不是「运行时真能过」。**

而运行时真源是 `permission` 表：`load_role_permissions` 的语义是
「**表有该角色的 allow 行 ⇒ 全量以表为准，`perms.PERMISSIONS` 字典被完全盖住**」
（`perms.py:174-178`）。`0056` seed 给四角色都写了行 ⇒ 表恒权威 ⇒
**任何只改字典不改表的加键动作，在 prod/staging 都是静默无效的**。

批 77 恰好就是这么踩的：字典加了 `live_control`，表没加 ⇒ 8 个实盘面端点
（建/启/停/删任务、解冻、解冻预览、启停策略进程）对**所有**角色 403，
**连 admin 自己都过不去**。而全部既有测试都是绿的。

## 本闸门钉三条

1. **覆盖性**：每个挂 `require_perm(k)` 的路由，`k` 必须被**至少一个角色**在表路径下持有
   ——否则该端点是「谁都进不去」的死门（不是「权限收紧」，是「功能消失」）。
   唯一例外：显式 `_NO_HOLDER_OK`（有意的全锁键，须逐条写理由）。
2. **多面同键**：批 77 的 `LIVE_ACTIONS` 在**表解析结果**下也必须成立（trader+admin 可，
   analyst+viewer 不可）——这是源码契约闸门**测不到**的那一半。
3. **字典/表一致性收窄面**：`perms.PERMISSIONS` 声明的键 ⊄ 表行时告警式断言
   （新增键没配套 seed 迁移 = 本 P0 同型病复发）。
"""
import re
from unittest.mock import MagicMock, patch

import src.data_platform.perms as perms_mod


# ——— 生产形状的表内容（`0056` seed 原样 + `0061` alerts_config + `0091` 删 account_keys） ———
# 这份 fixture 是**故意硬编码**的：它模拟 prod/staging 的 permission 表。若将来有迁移改了
# seed，本表**必须同步改**——这正是本闸门想要的摩擦（seed 与字典脱钩就是这个 P0 的成因）。
_PROD_SHAPED_ROWS = [
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
]

# 迁移 0124 补种的键（本闸门要证明「补种后成立」）——
# 顺序敏感：先证**补种前的形状会红**（见 TestGateCatchesRegression），再证补种后绿。
_MIGRATION_0124_ADDS = [("admin", "live_control", "allow"), ("trader", "live_control", "allow")]

# 有意为之的「全局零持有」键——每条必须写理由，防闸门被当成绊脚石而整体关掉。
# 当前为空：真源 `API_PERM_KEYS` 的 14 键都该有人持有（batch33b 注册表闸另有 170+ 绑定断言）。
_NO_HOLDER_OK: dict[str, str] = {}


def _resolve(rows):
    """用**真** `load_role_permissions` 解析给定表行 → {role: perm_set}。

    不复刻逻辑（那是测试自己骗自己）：直接打桩 `get_conn` 喂行，断言真函数的输出。

    ⚠️ **必须还原 `_PERM_CACHE`**（本函数修过一次真缺陷）：
    `load_role_permissions` 解析成功后会**写入 60s 有效的进程级缓存**。若本函数留下
    这层缓存，后续任何依赖「自行打桩 DB 再解析」的测试（如
    `test_batch76_unfreeze_surface.py::TestUnfreezeAuthGate`）会**直接读到本 fixture 的
    角色集**而不是它自己 mock 的 DB ⇒ 假红/假绿。修前实况：本文件与其同跑，
    `test_admin_allowed`/`test_trader_allowed` 得 403（读到本 fixture 的窄集），
    **单独跑却过**——典型跨文件缓存污染。
    """
    saved = dict(perms_mod._PERM_CACHE)      # at / roles 两槽
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = rows
    perms_mod._PERM_CACHE.update(at=0.0, roles=None)
    try:
        with patch("src.data_platform.db.get_conn", return_value=conn):
            return perms_mod.load_role_permissions()
    finally:
        perms_mod._PERM_CACHE.update(saved)  # 归还调用方原有缓存状态


def _post_0124():
    return _PROD_SHAPED_ROWS + _MIGRATION_0124_ADDS


def _collect_perm_bindings():
    """扫全部路由源码，抽 `require_perm("k")` 字面量 → {key: [endpoint 描述]}。

    真源=源码文本（与 batch33b `scan_perm_bindings` 同思路，此处自抽以保持测试独立）。
    """
    import pathlib
    import src.web_api.routes as routes_pkg

    out: dict[str, list[str]] = {}
    root = pathlib.Path(routes_pkg.__file__).parent
    for pyf in sorted(root.glob("*.py")):
        text = pyf.read_text(encoding="utf-8")
        for m in re.finditer(r'require_perm\(\s*["\']([a-z_]+)["\']\s*\)', text):
            out.setdefault(m.group(1), []).append(f"{pyf.name}:{text[:m.start()].count(chr(10)) + 1}")
    return out


class TestEveryPermKeyIsHoldable:
    """① 覆盖性：每个被 require_perm 引用的键，表路径下必须**至少有一个角色**持有。

    反面=「谁都进不去的死门」——这不是权限设计，是功能消失（本 P0 的 8 端点形态）。
    """

    def test_all_keys_holdable_after_0124(self):
        roles = _resolve(_post_0124())
        held = set()
        for perms in roles.values():
            held |= perms
        bindings = _collect_perm_bindings()
        assert bindings, "没扫到任何 require_perm 绑定——扫描逻辑坏了（假绿）"

        orphans = {k: v for k, v in bindings.items()
                   if k not in held and k not in _NO_HOLDER_OK}
        assert not orphans, (
            "以下 perm 键在任何角色下都无人持有 ⇒ 对应端点是 403 死门（非权限收紧，是功能消失）:\n"
            + "\n".join(f"  {k}: {', '.join(v[:4])}" for k, v in sorted(orphans.items()))
            + "\n修法：加 seed 迁移补 permission 表行（字典兜底在表有行时被完全盖住）")

    def test_admin_holds_every_declared_key(self):
        """admin 派生自注册表全键（`set(API_PERM_KEYS)`）——表路径下也应如此。

        白名单式例外：admin 在表路径下**真可以**被界面撤键（那是 UI 自由），
        故此处只钉「注册表全键都能被解析出来」，不钉 admin 的行内容。
        真正防回归的是上面的 orphans 断言。
        """
        from src.data_platform.perm_registry import API_PERM_KEYS
        roles = _resolve(_post_0124())
        # 字典侧：admin = 注册表全键（派生式，不应漂移）
        assert perms_mod.PERMISSIONS["admin"] == set(API_PERM_KEYS), \
            "admin 字典未与注册表同步（admin 应为 set(API_PERM_KEYS) 派生）"
        # 表侧：admin 行须 ⊇ 全部注册表键中「非 UI 撤权」的键——此处只断言关键实盘面键
        assert "live_control" in roles["admin"], "admin 表路径下须持 live_control"


class TestLiveControlEffectiveInTablePath:
    """② 批 77 多面同键契约的**表路径**半边（源码闸门测不到的那半）。"""

    def test_trader_and_admin_hold_live_control(self):
        roles = _resolve(_post_0124())
        assert "live_control" in roles["admin"], "admin 无 live_control ⇒ 实盘面 8 端点全 403"
        assert "live_control" in roles["trader"], "trader 无 live_control ⇒ 实盘面 8 端点全 403"

    def test_analyst_and_viewer_do_not_hold_live_control(self):
        roles = _resolve(_post_0124())
        assert "live_control" not in roles["analyst"], \
            "analyst 不得持 live_control（批 77 §一 原则：只回测/实盘测试，不执行实盘交易）"
        assert "live_control" not in roles["viewer"]

    def test_live_control_bound_endpoints_are_live_surface(self):
        """钉住 `live_control` 的绑定集 = 实盘面 7 动作 + 解冻预览（源=迁移注释同一份清单）。"""
        bindings = _collect_perm_bindings()
        assert bindings.get("live_control"), "无端点挂 live_control——批 77 拆分被回退了？"
        # 数量守卫：批 77 落地时是 8 处（trading.py 6 + strategy.py 2）。
        # 新增/删除须同步迁移注释与批 77 文件——此处故意用 >= 防脆断，用 == 防误加。
        assert len(bindings["live_control"]) == 8, (
            f"live_control 绑定数 {len(bindings['live_control'])} ≠ 8："
            f"{bindings['live_control']}——若是有意增删请同步本断言与 0124 迁移注释")


class TestMigration0124Shape:
    """③ 迁移文件本身的形状守卫（防「改了字典忘了迁移」复发）。"""

    def test_migration_file_exists_and_is_head_after_0123(self):
        import pathlib
        mf = (pathlib.Path(__file__).resolve().parents[1]
              / "migrations" / "versions" / "0124_seed_live_control.py")
        assert mf.exists(), "0124 迁移文件缺失"
        text = mf.read_text(encoding="utf-8")
        assert 'revision: str = "0124"' in text
        assert 'down_revision: Union[str, None] = "0123"' in text
        # 迁移用 f-string 拼角色名（`f"VALUES ('role', '{role}', ..."`），
        # 故断言元组 `_HOLDERS` 而非字面 `'admin'`——按符号名断言，别按渲染后的形态。
        assert '_HOLDERS = ("admin", "trader")' in text, "0124 持有者集被改/未定义"
        assert "ON CONFLICT DO NOTHING" in text, "0124 须幂等（用户可能已在界面勾选）"
        assert 'resource=\'live_control\'' in text, "0124 须自删该键行（downgrade 对称）"

    def test_every_api_key_has_a_seed_source(self):
        """③a 字典里的每个 api 键，必须在**某个迁移的 seed** 或 **代码兜底字典**中可追溯。

        修前我的首版断言写错了判据（拿 `'{k}'` 单引号抽种子文本 ⇒ 误报 11 键"零 seed"）——
        真源其实是 `0056_permission.py` 的 `_ROLE_PERMS`，那里键是**双引号**（`{"read", ...}`）。
        教训：抽源码字面量必须按**该文件的实际写法**抽，否则闸门会因假阴性而常年被忽略。

        本断言改判**双引号 + 单引号双形态**，且以「键 ∈ `0056` seed 集 ∪ 后续迁移补种集」为准。
        """
        import pathlib
        versions = (pathlib.Path(__file__).resolve().parents[1]
                    / "migrations" / "versions")
        seed_text = "\n".join(f.read_text(encoding="utf-8")
                              for f in versions.glob("*.py"))
        from src.data_platform.perm_registry import API_PERM_KEYS
        missing = [k for k in API_PERM_KEYS
                   if f"'{k}'" not in seed_text and f'"{k}"' not in seed_text]
        assert not missing, (
            f"以下 api 键在全部迁移里零 seed：{missing}——"
            "表有行时字典被盖住，这些键在 prod/staging 可能从未生效（本 P0 同型病）")


class TestGateCatchesRegression:
    """⭐ 反证：**补种前**的形状必须让本闸门红——否则闸门是装饰品。

    这正是批 77 的真实历史形状（字典有 live_control、表没有）。
    """

    def test_pre_0124_shape_has_orphan_live_control(self):
        roles = _resolve(_PROD_SHAPED_ROWS)   # 补种前
        held = set()
        for perms in roles.values():
            held |= perms
        # 这就是 P0：live_control 被 8 个端点 require，却无人持有
        assert "live_control" not in held, "fixture 失真：补种前形状竟已含 live_control"
        bindings = _collect_perm_bindings()
        assert "live_control" in bindings, "源码确实 require 了——证「声明有、真源无」的撕裂"
        assert bindings["live_control"], "绑定集不应为空"

    def test_dict_holds_but_table_does_not_pre_0124(self):
        """撕裂的直接证据：字典有键、表路径解析结果没有 ⇒ 字典被盖住。"""
        assert "live_control" in perms_mod.PERMISSIONS["admin"]   # 批 77改了字典
        assert "live_control" in perms_mod.PERMISSIONS["trader"]
        roles = _resolve(_PROD_SHAPED_ROWS)                       # 表没改
        assert "live_control" not in roles["admin"]               # ⇒ 静默无效
        assert "live_control" not in roles["trader"]

    def test_removing_live_control_from_0124_breaks_gate(self):
        """把 0124 的补种摘掉（模拟「迁移写漏」）⇒ ① 的 orphans 断言必须红。"""
        roles = _resolve(_PROD_SHAPED_ROWS)
        held = set()
        for perms in roles.values():
            held |= perms
        bindings = _collect_perm_bindings()
        orphans = {k for k in bindings if k not in held and k not in _NO_HOLDER_OK}
        assert "live_control" in orphans, "闸门无效：摘掉补种后 orphans 竟没抓到 live_control"
