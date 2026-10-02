"""补种 `paper_trade` 键 + nav 维行（批 86-B）。

**为什么必须有这个迁移（批 77 P0 的直接教训）**：
`perms.load_role_permissions` 的语义是「**表有该角色的 allow 行 ⇒ 全量以表为准，
字典被完全盖住**」（`perms.py:174-178`）。prod/staging 的 `permission` 表自 `0056` seed 起
就有四角色 × api 维行 ⇒ **`perms.PERMISSIONS` 字典永不生效**。

⇒ 新键若只加进注册表与字典、**不加表行**，则该键在**任何**角色下都不存在（连 admin 亦然）
⇒ 所有 `require_perm("paper_trade")` 的端点全 403。批 77 就是这么炸的（`live_control`，
用 `0124` 补的）。**本迁移是那条教训的直接应用。**

**同理适用于 nav 维**（本批新立据）：`perms.load_nav_map` 直读 `permission` 表 nav 维、
**无字典兜底**，缺省（无行）= `readwrite`。⇒ 要把 `live-task` 对 analyst 设成 `hidden`，
**必须补表行**，只在注册表加条目是不够的。

**本迁移落两组行**：

1. api 维 `paper_trade`：analyst / trader / admin = allow（**viewer 无**）
2. nav 维：
   - `paper-trade` = readwrite × (analyst, trader, admin)   ← 新菜单项
   - `paper-trade` = **hidden × viewer**                    ← §3.2 矩阵裁定
   - `live-task`  = hidden × (analyst, **viewer**)          ← 治理原则（本批核心裁定）

**为什么 viewer 也有两行 hidden（设计矩阵 §3.2 的显式裁定）**：
初版实现曾以「viewer 的读门已是 `live_control`/`paper_trade`，hidden 行属冗余」为由
不写 viewer 行——**这是偏离已裁定矩阵的自作主张，已纠正**。写行的理由不是访问控制，
而是**声明语义**：viewer 无行 = 缺省 readwrite = nav 维声称「viewer 可用这两页」，
与后端 403 矛盾（「真源说可读、API 说不行」的撕裂态）。显式 hidden 让 nav 维的
声明与 api 维的门一致，且覆盖性闸门可以对矩阵逐格断言（无行则无从断言）。
analyst 的 hidden 额外是**治理要求本身**（菜单上就不该出现——造策略的人≠验策略的人）。

Revision ID: 0126
Revises: 0125
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0126"
down_revision: Union[str, None] = "0125"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None

# api 维持有者（viewer 刻意不在列——只读角色不给纸上交易能力，见裁决 §五 矩阵）
_API_HOLDERS = ("analyst", "trader", "admin")

# nav 维：新菜单项对三角色 readwrite
_NAV_RW_HOLDERS = ("analyst", "trader", "admin")

_SEED_NOTE = "批86-B 纸上交易（批77 P0 教训：加键必补表行）"


def _seed_api(resource: str) -> None:
    for role in _API_HOLDERS:
        op.execute(
            "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, note) "
            f"VALUES ('role', '{role}', 'api', '{resource}', 'allow', '{_SEED_NOTE}') "
            "ON CONFLICT DO NOTHING")


def _seed_nav(resource: str, effect: str, roles: Sequence[str]) -> None:
    for role in roles:
        op.execute(
            "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, note) "
            f"VALUES ('role', '{role}', 'nav', '{resource}', '{effect}', '{_SEED_NOTE}') "
            "ON CONFLICT DO NOTHING")


def upgrade() -> None:
    # ① api 维（缺即静默失效——四条齐动的第 3 条）
    # ⚠️ 原拟同时补 `strategy_pretest`（「策略前测」职能名），**落码时撤销**：
    #    覆盖性闸门实测它是零消费者的死键（无任何端点绑定）——职能标注不该占用 api 键
    #    （键=端点准入）。analyst 的前测职责由 `paper_trade` 承载。详见 perm_registry 注。
    _seed_api("paper_trade")        # 跑纸上任务的能力（=analyst 的「前测」入口）

    # ② nav 维：新菜单项 readwrite × 三角色；viewer 显式 hidden（§3.2 矩阵——
    #    与 api 维无行=403 一致，避免「nav 声称可用、API 拒绝」的撕裂态）
    _seed_nav("paper-trade", "readwrite", _NAV_RW_HOLDERS)
    _seed_nav("paper-trade", "hidden", ("viewer",))

    # ③ nav 维：实盘任务对 analyst+viewer hidden（治理原则——造策略的人≠验策略的人；
    #    viewer 同 §3.2 矩阵显式 hidden）
    #    这一步是本批的**产品语义**落点，不只是权限微调：analyst 菜单里
    #    「实盘」组只剩「纸上交易」一项（达成威廉姆「一个角色的功能集中一组」）。
    _seed_nav("live-task", "hidden", ("analyst", "viewer"))


def downgrade() -> None:
    # 行级删（permission 是共享表，勿 drop）——与 0061/0091/0124 先例对称。
    # 注意（同 0124 的告诫）：downgrade 会把「用户后来在界面自己勾的这两键」一并删掉。
    # 这是可接受的（downgrade 本就回到 0125 的语义：这两键不存在），正因如此，
    # **回滚后再升级必须重跑本迁移**，否则又回到 403 死态。
    op.execute(
        "DELETE FROM permission WHERE dimension='api' "
        "AND resource = 'paper_trade'")
    op.execute(
        "DELETE FROM permission WHERE dimension='nav' "
        "AND ((resource='paper-trade') OR (resource='live-task' AND subject_id IN ('analyst','viewer')))")
