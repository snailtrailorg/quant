"""批 101b：币安永续盘中 bar（小时 / 1min / 15min）——**功能验证档**。

**背景与范围（威廉姆 2026-10-06 裁决）**：`bar_1h`/`bar_1min`/`bar_15min` 三表自 0064
建表起**从未落过一行**（唯一的分钟写入路径 `astock_minute` 是 XTP 自攒，未启用）。
本批把币安批量站的盘中 K 线接进来，验证「同 adapter 多 interval + 多 bar 表」这条链。

**本迁移只插配置行**（expand-only：无 DDL、无 DELETE/RENAME）：sync_config 三行 +
sync_kind_config 三行。落表由 `freq` 决定（`bar_{freq.lower()}`，与 db.save_bars 的
insert 模板同构），freq 由 `engine._BINANCE_BAR_SPECS` 声明——**代码是声明面**，
本迁移的 `pg_table` 须与之一致（`tests/test_batch101b_*` 真库对账钉守，防两处真源漂移）。

**🔴 `schedule='manual'` 不是笔误，是存储闸门**（本批的存储决策，勿"顺手"改成 cron）：
- 批量站是**日频发布**（T+1，实测还有 >1 天滞后），cron 化＝每天新增一天数据、**只增不删**；
- prod 实测 `/dev/vda3 40G，可用 8.3G`；1min ≈ **450MB/天** ⇒ **约 18 天打满磁盘**
  （1h≈53MB/天→约 160 天；15min≈30MB/天→约 276 天）。磁盘打满会拖垮整个平台。
- 威廉姆批准的档位是「**有界占用**」（hourly 7 天 + 1min/15min 各 1 天，合计 <0.6GB），
  日调度会把它变成无界 ⇒ 与已批准的意图冲突。
- `schedule='manual'` 的效果：`data_sync_scheduler` 显式跳过（`schedule == "manual"`），
  但 `enabled=true` 使 **UI 手动触发 / 回补**可用（`sync()` 只在 disabled 时拒绝）。
  即：**能点、不自动长**。`start_floor` 同时把回补选择器的下限钉住（UI `backfillDisabledDate`）。
- 健康面噪声为 0：tier 新鲜度只覆盖 `TIER1_SYNC_IDS`/`TIER2_ALL_TABLES`（本三键不在内），
  DataManage 状态列对 enabled 且未跑的行显示 `idle`（非红）。
- **扩盘后**：把想要的项 schedule 改成 `'30 8 * * *'`（与日线同刻）+ 放宽 `start_floor`，
  即得常规增量。**这两个动作是运维显式决策**，不由本迁移代做。

**DataKind 收窄（记录在案）**：27 词 DataKind 立法**没有 hour 粒度词**，故 1h 与 1min/15min
同归 `bar_minute`（盘中 bar 族，时态含 historical）；落表靠 `pg_table` 区分。加新 DataKind
是立法动作（contract 词表 + `KIND_CAP_CLASS` + 三条钉），不搭本批的车。

**写法**：`op.execute(<内联字面量 SQL>)`（`--sql` 离线渲染安全，同 0132/0133）。
`start_floor` 用 `CURRENT_DATE` 相对量——「执行日前推 N 天」，正是迁移写入**当日值**的语义。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0134"
down_revision: Union[str, Sequence[str], None] = "0133"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (sync_id, 中文名, 目标表, 首跑/回补下界＝执行日前推天数, 说明)
_ROWS = [
    ("binance_perp_hourly", "加密永续小时线", "bar_1h", 7,
     "币安 USDT-M 永续 1h（data.binance.vision 批量 ZIP，T+1）。功能验证档：manual 调度。"),
    ("binance_perp_1min", "加密永续1分钟线", "bar_1min", 1,
     "币安 USDT-M 永续 1m。功能验证档：manual 调度；**日调度≈450MB/天，须先扩盘**。"),
    ("binance_perp_15min", "加密永续15分钟线", "bar_15min", 1,
     "币安 USDT-M 永续 15m。功能验证档：manual 调度；先扩盘再 cron 化。"),
]


def upgrade() -> None:
    for sid, name, table, floor_days, desc in _ROWS:
        # 落表＝table（bar_1h/bar_1min/bar_15min）；其后缀即 freq，与引擎 _BINANCE_BAR_SPECS 同源声明
        op.execute(
            "INSERT INTO sync_config (id, name, tushare_api, pg_table, data_type, sync_mode, "
            "schedule, enabled, trade_day_filter, provider, supports_backfill, start_floor, "
            "description) VALUES ("
            f"'{sid}', '{name}', 'binance.klines', '{table}', 'crypto', 'incremental', "
            f"'manual', true, 'none', 'binance', true, "
            f"(CURRENT_DATE - INTERVAL '{floor_days} days')::date, '{desc}') "
            "ON CONFLICT (id) DO NOTHING")
        op.execute(
            "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, "
            "float_cols, text_cols, rebuild) VALUES ("
            f"'{sid}', 'bar_minute', 'perp', '{table}', "
            "'{symbol,ts}', '{open,high,low,close,volume,amount}', '{}', 'incremental') "
            "ON CONFLICT (sync_id) DO NOTHING")


def downgrade() -> None:
    for sid, *_ in _ROWS:
        op.execute(f"DELETE FROM sync_kind_config WHERE sync_id = '{sid}'")
        op.execute(f"DELETE FROM sync_config WHERE id = '{sid}'")
