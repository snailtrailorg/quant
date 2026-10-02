"""补种 `live_control` 键（批 77 拆分遗漏的 permission 表行）。

**病灶**：批 77 把实盘面 8 端点从 `strategy_control` 迁到新键 `live_control`，但只改了
`perms.PERMISSIONS`（**缺省字典**）。而 `perms.load_role_permissions` 的语义是
「**表有该角色的 allow 行 ⇒ 全量以表为准，字典被完全盖住**」（`perms.py:174-178`）。
prod/staging 的 `permission` 表自 `0056` seed 起就有四角色 × api 维行 ⇒ **字典永不生效**。

结果：`live_control` 在**任何**角色下都不存在（连 admin 自己也一样）⇒ 8 端点全 403：

    create/start/stop/delete/unfreeze_live_task  (trading.py)
    get_unfreeze_request                          (trading.py)
    start/stop_strategy                           (strategy.py)

即：**建不了实盘任务、启停不了、解不了冻**。批 76 的解冻面在 UI 上是死按钮，
而批 77 自己的 `TestToolGatingContract` **只扫源码字面量**（`require_perm("live_control")`
确实在源码里），断言「代码声明是对的」，**从不解析 DB 生效权限集** ⇒ 全线绿。

**为什么单靠 UI 勾选不够（用户已在界面删过相关行）**：PermMatrix 保存走
`DELETE 该 role 全 api 行 + INSERT 提交集`（`auth_routes.py:262-273` 全量重写）。
用户删掉的是 `halt`/`live_trading_control` 的**行**——那次全量重写把当时**前端提交集**
里的键逐条写回，而前端 `permGroups` 当时**还没有** `live_control` 可勾
（批 77 才加进 `trading` 组），所以无论怎么勾都写不出 `live_control` 行。
⇒ **必须由迁移补行**，不能只靠界面。

**本迁移**：为 admin/trader 各补一条 `live_control` allow 行（幂等 `ON CONFLICT DO NOTHING`）。
- 幂等：`permission` 有 `UNIQUE (subject_type, subject_id, dimension, resource, effect)`，
  重复执行零副作用——用户若已在界面勾选，本迁移是 no-op。
- **不**动 analyst/viewer（原则：analyst 只回测与实盘测试，不执行实盘交易——批 77 §一）。
- **不**碰 `halt` / `live_trading_control`（批 77 续已收回 admin 独占；本批只管 `live_control`）。

Revision ID: 0124
Revises: 0123
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0124"
down_revision: Union[str, None] = "0123"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None

# 实盘面档=trader+admin（批 77 §四 权限矩阵；analyst/viewer 零行）
_HOLDERS = ("admin", "trader")


def upgrade() -> None:
    for role in _HOLDERS:
        op.execute(
            "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, note) "
            f"VALUES ('role', '{role}', 'api', 'live_control', 'allow', '批77 拆分补种（批76b 发现）') "
            "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # 行级删（permission 是共享表，勿 drop）——与 0061/0091 先例对称。
    # 注意：downgrade 会把「用户后来在界面自己勾的 live_control」一并删掉。
    # 这是可接受的（downgrade 本就是回到 0123 的语义：该键不存在），但正因如此，
    # **回滚后再升级必须重跑本迁移**，否则又回到 403 死态。
    op.execute(
        "DELETE FROM permission WHERE subject_type='role' AND dimension='api' "
        "AND resource='live_control'")
