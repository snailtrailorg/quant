# EXPAND-CONTRACT: phase=expand pair=None
"""批 171b：停牌日历——`suspend_calendar` 表 + `suspend_d_sync` 每日增量项（expand-only）。

任务＝`flow/任务/批171b-停牌日历与期望集净化.md`（v3.1 定稿，三轮双盲审+快核）。
suspend_d 实测（4 日真调 13/7/22/30 行）：4 列 ts_code/trade_date/suspend_timing/suspend_type；
timing 全 NULL（=全天停牌）；type ∈ {S,R}（R=复牌，当日实有 bar——净化只认 S 行）。

**为什么独立表**：trade_cal 是 (exchange,cal_date) 族级粒度、sync_gap 排除段是对账结论——
停牌是 (symbol,date) 标的级**输入事实**，粒度与语义都不同（实体设计双判据：无继承不揉合）。

**本迁移（纯 expand：新表 + 两行配置）**：
1. 新表 `suspend_calendar`——PK=(ts_code, trade_date)；suspend_type 原样保留 S/R；
   full_day 布尔（timing IS NULL ⇒ true）。
2. `sync_config.suspend_d_sync`——kind=`suspend`（DataKind 既有词，contract.py:35）；
   `retention='2026-10-01'` 地板（批 108 F-1 防首跑全史——SOURCE_EARLIEST=2010-01-01
   会被 max 夹住，0143 同款先例）；`trade_day_filter='trade_day'`；16:35 档（盘后出停牌
   登记，先于面板/对账消费；上游发布时刻未核实——overlap=3 兜底，不稳可后移）。
3. `sync_kind_config.suspend_d_sync`——归置 `(suspend, None)` → `suspend_calendar`。
   列形状：full_day 走 text_cols（str(True)→'True'::boolean，PG 合法实测）；transform
   在 engine `_TIER1_BATCH_EXTRA` 边车注入（本迁移不含代码）。

**downgrade**：删两行配置 + DROP TABLE（对称回滚）。
**写法（仓例）**：全部 `op.execute(<内联字面量>)`（离线渲染安全，同 0132~0142）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0168"
down_revision: Union[str, Sequence[str], None] = "0167"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYNC_ID = "suspend_d_sync"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS suspend_calendar (
            ts_code      text NOT NULL,
            trade_date   date NOT NULL,
            suspend_type text NOT NULL,
            -- full_day 用 text（'True'/'False'）而非 boolean：工厂列映射只分 numeric/text
            -- 二桶（107 守门按 DDL 分桶校验归置行），boolean 列落不进任何桶 ⇒ 走 text 桶；
            -- PG 'True'::text 原样存取，净化口 WHERE full_day = 'True'（等价布尔语义）。
            full_day     text NOT NULL DEFAULT 'True',
            updated_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_suspend_calendar PRIMARY KEY (ts_code, trade_date)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_suspend_calendar_date "
        "ON suspend_calendar (trade_date)")
    # sync_config 行（幂等：ON CONFLICT DO NOTHING——复跑不覆盖运维编辑）
    # supports_backfill=true：工厂 handler 真读 backfill_from（批 99 双向钉守）
    # retention='2026-10-01'：防首跑全史（~4000 次调用）——历史回补属运维动作另立
    op.execute(
        "INSERT INTO sync_config (id, name, tushare_api, pg_table, sync_mode, "
        "schedule, trade_day_filter, enabled, supports_backfill, retention, description) VALUES ("
        "'suspend_d_sync', '每日停牌登记', 'pro.suspend_d', 'suspend_calendar', "
        "'incremental', '35 16 * * 1-5', 'trade_day', 'true', 'true', '2026-10-01', "
        "'全市场停牌/复牌登记快照（suspend_d，~15行/日）。批171b起停牌日历真源："
        "integrity 期望减项与 per-symbol 对账净化只认 S+full_day 行') "
        "ON CONFLICT (id) DO NOTHING")
    # 归置行：kind=suspend（DataKind 既有词——suspend_d 不是合法 kind，import 期即炸）
    op.execute(
        "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, "
        "float_cols, text_cols, rebuild) VALUES ("
        "'suspend_d_sync', 'suspend', NULL, 'suspend_calendar', "
        "'{ts_code,trade_date}', '{}', '{suspend_type,full_day}', 'incremental') "
        "ON CONFLICT (sync_id) DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM sync_kind_config WHERE sync_id = '{_SYNC_ID}'")
    op.execute(f"DELETE FROM sync_config WHERE id = '{_SYNC_ID}'")
    op.execute("DROP TABLE IF EXISTS suspend_calendar")
