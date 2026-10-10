# EXPAND-CONTRACT: phase=expand pair=0143
"""批 117：ST 官方名单 fail-closed——`st_list` 表 + `st_list_sync` 每日快照项（expand-only）。

任务＝`flow/任务/批117-ST官方名单fail-closed.md`（步 0 探查定案：官方接口
`pro.stock_st(trade_date=)`，2026-10-09 实测全市场 201 行；namechange 单次上限 10000 截断）。

**为什么换源**：原 ST 判定链=`namechange` 曾用名派生（`engine._derive_st_states`，戴帽滞后）
+ perms「无档=非 ST」fail-open。本批切**官方名单快照**（每交易日同步落 `st_list`），
perms 无档改 fail-closed（表空=冷启动保护期例外，见 perms.py 注）。

**本迁移（纯 expand：新表 + 两行配置，无 DROP/RENAME/ALTER）**：
1. 新表 `st_list`——列=stock_st 实测列（ts_code/name/trade_date/type/type_name），
   PK=(trade_date, ts_code)（每日全量快照，同 stk_limit 族按日累积）。
2. `sync_config.st_list_sync`——kind=`featured_daily`（与 stk_limit 同族「按日全市场快照表」，
   归置真相在 sync_kind_config）、`trade_day_filter='trade_day'`（交易日才有名单）、
   晚间档 `10 18 * * 1-5`（盘后出名单，与 0045 族错峰：16:30/18:00/18:15/18:30 之后）。
   `retention` 不设（NULL=未声明，不臆造——名单行数 ~200/日，无存储压力）。
3. `sync_kind_config.st_list_sync`——归置 `(featured_daily, st_list)` → `st_list`。

**downgrade**：删两行配置 + DROP TABLE st_list（对称回滚）。
`security_state(st)` 若已由新派生写入不随本迁移回滚（数据与配置解耦，同 0136 口径）。

**写法（仓例）**：全部 `op.execute(<内联字面量>)`（`--sql` 离线渲染安全，同 0132~0141）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0142"
down_revision: Union[str, Sequence[str], None] = "0141"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYNC_ID = "st_list_sync"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS st_list (
            trade_date text NOT NULL,
            ts_code    text NOT NULL,
            name       text,
            type       text,
            type_name  text,
            CONSTRAINT pk_st_list PRIMARY KEY (trade_date, ts_code)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_st_list_code ON st_list (ts_code, trade_date)")
    # sync_config 行（幂等：ON CONFLICT DO NOTHING——复跑不覆盖运维编辑）
    # supports_backfill=true：tier1 工厂 handler 真读 backfill_from（批 99 双向钉守）
    op.execute(
        "INSERT INTO sync_config (id, name, tushare_api, pg_table, data_type, sync_mode, "
        "schedule, trade_day_filter, enabled, supports_backfill, description) VALUES ("
        "'st_list_sync', '每日官方ST名单', 'pro.stock_st', 'st_list', 'astock', "
        "'incremental', '10 18 * * 1-5', 'trade_day', 'true', 'true', "
        "'全市场ST/风险警示名单快照（stock_st，~200行/日）。批117起ST判定真源"
        "（原namechange派生降级历史参考）；perms无档fail-closed依赖本表非空') "
        "ON CONFLICT (id) DO NOTHING")
    # 归置行：kind=featured_daily（按日全市场快照族，与 stk_limit 同范式）
    op.execute(
        "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, "
        "float_cols, text_cols, rebuild) VALUES ("
        "'st_list_sync', 'featured_daily', 'st_list', 'st_list', "
        "'{trade_date,ts_code}', '{}', '{name,type,type_name}', 'incremental') "
        "ON CONFLICT (sync_id) DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM sync_kind_config WHERE sync_id = '{_SYNC_ID}'")
    op.execute(f"DELETE FROM sync_config WHERE id = '{_SYNC_ID}'")
    op.execute("DROP TABLE IF EXISTS st_list")
