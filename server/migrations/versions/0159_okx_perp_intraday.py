# EXPAND-CONTRACT: phase=expand（新增行迁移——无既有消费者破坏，contract 随表退役批次走，同 0145 惯例）
"""批 116：OKX 永续盘中 bar（hourly / 1min / 15min）——功能验证档（0134 范式）。

本迁移只插配置行（expand-only）：sync_config 三行 + sync_kind_config 三行。
落表由 freq 决定（bar_{freq.lower()}）；freq 由 engine._OKX_BAR_SPECS 声明——
代码是声明面，pg_table 须与之一致（101b 范式真库对账钉守）。

🔴 与 0134 的两处差异（批 116 v3 立法）：
- **窗口地板写 `retention` 列**（0139 后引擎唯一真读列；`start_floor` 已死列零读点
  ——0134 的列写法不可照抄，见 `engine._get_config` SELECT）。
- OKX 走代理出口（AWS relay 单点，20req/2s IP 级）：1min 档一轮 ≈12min（1440 根/天
  ÷100/页=15 页 ×485 标的）——schedule='manual' 存储与代理负载双闸门，cron 化＝
  用户显式决策（relay 带宽实测+存储评估后）。
manual 语义同 0134：能点、不自动长；健康面零噪声（三键不在 TIER 清单）。
DataKind 无 hour 粒度词，1h 同归 bar_minute（0134 记录在案，不另立法）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0159"
down_revision: Union[str, Sequence[str], None] = "0158"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (sync_id, 中文名, 目标表, retention 下界＝执行日前推天数, 说明)
_ROWS = [
    ("okx_perp_hourly", "OKX永续小时线", "bar_1h", 7,
     "OKX USDT 永续 1H（www.okx.com history-candles，代理出口）。功能验证档：manual 调度。"),
    ("okx_perp_1min", "OKX永续1分钟线", "bar_1min", 1,
     "OKX USDT 永续 1m。功能验证档：manual 调度；一轮≈12min（代理限频），cron 化须先评估 relay 带宽。"),
    ("okx_perp_15min", "OKX永续15分钟线", "bar_15min", 1,
     "OKX USDT 永续 15m。功能验证档：manual 调度。"),
]


def upgrade() -> None:
    for sid, name, table, floor_days, desc in _ROWS:
        op.execute(
            "INSERT INTO sync_config (id, name, tushare_api, pg_table, sync_mode, "
            "schedule, enabled, trade_day_filter, provider, supports_backfill, retention, "
            "description) VALUES ("
            f"'{sid}', '{name}', 'okx.candles', '{table}', 'incremental', "
            f"'manual', true, 'none', 'okx', true, "
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
