"""建 `paper_trade_log` + `trading_account.is_virtual` + 虚拟账户行（批 86-B）。

本迁移解决**两件互相咬合的事**，所以放在一起：

## 一、`paper_trade_log`——纸上成交的审计真源

paper 任务的「成交」是**假装**的（订单不进市场；`check_order` 通过后本地记账）。
必须有地方落这个事实，否则：
- **权益曲线无法回算**（「查看结果」= 任务权益曲线，是本批的核心交付）；
- **无法审计 paper 到底做了什么**（它走了完整风控链 ⇒ 决策链有审计价值）。

字段**刻意不复用 `trade_log`**：paper 成交没有 `order_id`/`trade_ref`（交易所产物），
硬塞进 `trade_log` 会让所有读它的地方（对账、归因、PnL）**静默混入假成交**。
⇒ 独立表 = 独立读方（只有权益曲线读它），物理上不可能污染实盘账。

## 二、`trading_account.is_virtual` + 虚拟账户行——风控预算隔离（裁决 §四/§7.3）

`flow/任务/批86B-命名裁决.md` §四 记录了一个**从业界文献检索出来的缺口**：
`quant67.com` 的「环境必须保证的不变量」表把「**不能与实盘共享风险预算**」列为 paper 的硬要求。

不隔离的后果：paper 的虚拟持仓/亏损**吃掉**实盘账户的额度（`single_position_pct`
按账户总值算、`daily_loss_limit` 按账户日盈亏算）⇒ 实盘被 paper 的虚拟盈亏**误冻结**，
且 paper 的验证结论不可信（它算的是「混了两个世界」的仓位）。

**修法不需要改 `risk.py`**：链路里所有预算读取**已经**按 `account_id` 分桶
（`_symbol_exposure(symbol, account_id)` `risk.py:162`、`_get_global_state(account_id)` `risk.py:308`）。
⇒ 只要给 paper 任务绑定一个**独立虚拟账户**，隔离自动成立。

### ⚠️ 建在 `trading_account` 而不是 `accounts`（一处必须记下的表选择）

`live_task.account_id` 指向 **`trading_account.id`**（批 83a 的 `0116` 从 `external_interface`
拆出的真源表；`list_live_tasks` 的 JOIN、`create_live_task` 的校验读的都是它）。
`accounts` 是**另一个**旧表（0042/0027，券商账户，批 55b 的账户下拉）——两者**不是同一张**。
**本迁移最初写成了 `accounts`，落码时自查发现并改正**（记此一笔，因为「看到 account 就以为是
`accounts` 表」是本仓很容易再犯的错——`schema_expectations.txt` 里两张表都叫得像个账户表）。

### ⚠️ `is_virtual` 这个标记为什么必须加

没有它，虚拟账户在账户下拉里与真实账户**不可区分**，管理员可能给一个 **live 任务**选上它
⇒ 该实盘任务的风控预算变成一个空的虚拟账户 ⇒ **风控形同关闭**。
这是加列的**真实理由**，不是洁癖。⇒ `GET /api/account` 与本批的账户下拉必须过滤它。

Revision ID: 0127
Revises: 0126
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0127"
down_revision: Union[str, None] = "0126"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None

# 虚拟账户定位串。`provider='paper'` 是**新造 provider 码**——真实账户这里是 xtp/binance/okx 等，
# 不会撞（`trading_account` 的 UNIQUE 是 (provider, account_key)，本行 account_key 留 NULL
# ⇒ 不参与唯一判定，多行也不冲突，见 0116 的 `ix_trading_account_account_key` 注）。
_VIRTUAL_PROVIDER = "paper"
_VIRTUAL_ACCOUNT_NAME = "纸上交易虚拟账户"


def upgrade() -> None:
    # ——— 一、paper_trade_log ———
    op.execute("""
        CREATE TABLE IF NOT EXISTS paper_trade_log (
            id           BIGSERIAL PRIMARY KEY,
            live_task_id INTEGER NOT NULL,
            ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
            symbol       TEXT,
            action       TEXT,
            volume       NUMERIC,
            price        NUMERIC,
            reason       TEXT
        )
    """)
    # 读方模式=「按任务取某时间窗的成交算权益曲线」⇒ (live_task_id, ts) 复合索引。
    # 单列 live_task_id 索引在时间窗查询上要回表排序，任务跑久了会退化。
    op.execute("CREATE INDEX IF NOT EXISTS idx_paper_trade_log_task_ts "
               "ON paper_trade_log (live_task_id, ts)")

    # ——— 二、trading_account.is_virtual ———
    op.execute("ALTER TABLE trading_account ADD COLUMN IF NOT EXISTS is_virtual "
               "BOOLEAN NOT NULL DEFAULT false")

    # ——— 三、虚拟账户行 ———
    # WHERE NOT EXISTS 幂等。capabilities NOT NULL ⇒ 给空数组（虚拟账户不申报能力：
    # 它的「能力」由 mode 决定，不由 provider 决定；空数组也天然避开 capabilities 白名单闸）。
    # market NOT NULL ⇒ 取 'astock'（paper 任务跑 A 股为主；这只是账户的市场归属标签，
    # 真实可交易范围由下方 account_permission 的三维放行决定）。
    op.execute(f"""
        INSERT INTO trading_account (name, provider, market, capabilities, enabled, is_virtual)
        SELECT '{_VIRTUAL_ACCOUNT_NAME}', '{_VIRTUAL_PROVIDER}', 'astock',
               ARRAY[]::text[], true, true
        WHERE NOT EXISTS (SELECT 1 FROM trading_account WHERE is_virtual = true)
    """)

    # ——— 四、虚拟账户的品种权限（account_permission 三维） ———
    # 必需：`create_live_task` 与 runner 启动前都调 `account_allows(account_id, symbol)`
    # （三维 category/exchange/board），无行=拒绝（`perms.py:117` fail-closed）。
    # 不给虚拟账户放行则**纸上任务一件都建不出来**。
    #
    # ⚠️ **三个 allowed_* 列是 NOT NULL 且默认 `'{}'`，而空数组的语义是「什么都不许」**
    #    （`perms.py:123` `attr.category not in (cats or [])`）——不是「不限」。
    #    首次落码时给 NULL 直接被 NOT NULL 约束拦下（IntegrityError），
    #    给了 `'{}'` 则是**静默的零权限**（建任务必 403），比报错更坏。
    #    ⇒ 必须显式填入真实可交易范围。取值同真实交易账户的现网形状（存量行实测）。
    op.execute("""
        INSERT INTO account_permission
            (account_id, allowed_categories, allowed_exchanges, allowed_boards,
             is_st_allowed, convertible_allowed, created_at, updated_at)
        SELECT ta.id,
               ARRAY['stock','etf','convertible','fund','reits']::text[],
               ARRAY['SHSE','SZSE','BSE']::text[],
               ARRAY['main','star','chinext','bse']::text[],
               true, true, now(), now()
        FROM trading_account ta
        WHERE ta.is_virtual = true
          AND NOT EXISTS (SELECT 1 FROM account_permission ap WHERE ap.account_id = ta.id)
    """)


def downgrade() -> None:
    # 逆序拆。先删 account_permission 行（FK），再删虚拟账户行（否则 is_virtual 列已无意义）。
    op.execute("DELETE FROM account_permission WHERE account_id IN "
               "(SELECT id FROM trading_account WHERE is_virtual = true)")
    op.execute("DELETE FROM trading_account WHERE is_virtual = true")
    op.execute("ALTER TABLE trading_account DROP COLUMN IF EXISTS is_virtual")
    op.execute("DROP INDEX IF EXISTS idx_paper_trade_log_task_ts")
    op.execute("DROP TABLE IF EXISTS paper_trade_log")
    # ⚠️ downgrade 会**丢弃全部纸上成交记录**（表被 drop），且会让既有 paper 任务的
    # account_id 指向一个已不存在的虚拟账户。可接受（回到 0126 语义：paper 概念不存在），
    # 但**回滚前必须**：① 停掉所有 paper 任务；② 若需保留验证结论，先自行导出 paper_trade_log。
    # 已在 playbooks/rollback-tasks.yml 批 86 注记写明。
