"""批183c：删 3 个零使用/重复索引（全库盘点结果）。

- idx_user_tokens_token：0003 建，与 UNIQUE user_tokens_token_key (token) 完全重复。
- idx_margin_code：0045 建 (ts_code, trade_date)，idx_scan=0（主键 (trade_date, ts_code)
  前缀不覆盖 ts_code，但当前无「按 ts_code 查 margin_detail」的代码——按 D18 §2.6 默认不建）。
- idx_system_log_level：0077 建 (level)，idx_scan=0。

Revision ID: 0188
Revises: 0187
"""
from alembic import op

revision = "0188"
down_revision = "0187"

_IDX = [
    ("idx_user_tokens_token", "user_tokens", "(token)"),
    ("idx_margin_code", "margin_detail", "(ts_code, trade_date)"),
    ("idx_system_log_level", "system_log", "(level)"),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _t, _cols in _IDX:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, tbl, cols in _IDX:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {tbl} {cols}")
