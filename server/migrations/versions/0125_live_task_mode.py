"""`live_task` 加 `mode` 列——纸上交易（paper trading）与实盘（live）的区分真源。

**背景（批 86-B）**：产品原先只有「实盘任务」一种任务，其下单与否完全由**全局总闸**
（`.env ENABLE_LIVE_TRADING` + `live_trading_config` 分项）决定——全开或全关，**无法 per-task**。
于是「用实时数据验证策略但不下单」（业界标准术语 = paper trading / 纸上交易）这一档
**在链路里没有载体**：回测的下一条路直接是全局实盘闸。

**命名依据**（见 `flow/任务/批86B-命名裁决.md`）：
- 值域取 `live` / `paper`——对齐业界运行模式枚举（quantform.io `paper`/`replay`/`live`、
  QAStrategy Backtesting/**Simulation**/Live、QMT 调试/回测/**模拟**/实盘）。
- 英文一律 `paper`：最通用、零歧义。`simulation` 与 `live` 在「实时」这点重叠易混；
  `test` 是无出处的自造词（原「实盘测试」）；`replay` 与回测撞语义。

**为什么加列而不是拆表**：paper 与 live 共用**全部**任务生命周期（创建/启停/冻结/心跳/日志/
systemd 单元/权限面），差异**只在「是否真下单」一个点**。拆表会把 8 个端点、冻结史、
心跳、单元安装全部复制一遍 ⇒ 明确否决（设计文档 §3.1）。

**默认值 `'live'` 的安全性**：存量任务全部是实盘任务 ⇒ `NOT NULL DEFAULT 'live'` 让迁移
零回填、零窗口期（不是「先给个占位值再改」的两步走——那个默认值**就是**存量行的正确语义）。

Revision ID: 0125
Revises: 0124
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0125"
down_revision: Union[str, None] = "0124"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None

# 值域锁（与代码侧 `MODE_LIVE`/`MODE_PAPER` 同步——改这里必须同步改 runner）
_LIVE = "live"
_PAPER = "paper"


def upgrade() -> None:
    # IF NOT EXISTS 幂等：重跑/中断后重来均安全（0042 先例同法）。
    op.execute(f"ALTER TABLE live_task ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT '{_LIVE}'")
    # CHECK 守卫（本仓立法：新枚举列必配 CHECK——防代码写进未定义值后静默落库）。
    # DO $$ 幂等 + **connamespace 限定当前 schema**：
    #   ⚠️ `pg_constraint` 是跨 schema 的全局目录，`WHERE conname=...` 不带命名空间限定时，
    #   **任何其他 schema 里的同名约束都会让本 schema 的创建被静默跳过**——往返脚本
    #   （scratch schema 彩排）实测逮到：mode 列建了、CHECK 没建、bogus 值直接落库。
    #   修法：connamespace 对齐 current_schema()（alembic 经 PGOPTIONS search_path 隔离时
    #   current_schema() 即 scratch，prod 即 public——两态都对）。
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint
                           WHERE conname = 'live_task_mode_chk'
                             AND connamespace = current_schema()::regnamespace) THEN
                ALTER TABLE live_task ADD CONSTRAINT live_task_mode_chk
                    CHECK (mode IN ('{_LIVE}', '{_PAPER}'));
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # 逆序拆：先约束后列。IF EXISTS 幂等。
    op.execute("ALTER TABLE live_task DROP CONSTRAINT IF EXISTS live_task_mode_chk")
    op.execute("ALTER TABLE live_task DROP COLUMN IF EXISTS mode")
    # ⚠️ downgrade 会丢弃所有 paper 任务的 mode 标记（回退后它们看起来像实盘任务）。
    # 这是可接受的（downgrade 本就是回到 0124 的语义：paper 这个概念不存在），
    # 但**回滚前必须先停掉所有 paper 任务**——否则回滚后一个「本以为在纸上验证」的任务
    # 会被当成实盘任务、在实盘闸开启时真的下单。这是本迁移唯一的危险面，已在
    # `playbooks/rollback-tasks.yml` 的批 86 注记里写明。
