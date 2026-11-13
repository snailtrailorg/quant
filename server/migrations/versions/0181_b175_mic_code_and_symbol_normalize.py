"""批175 L3（expand）：security_master.mic_code 回填 + position_snapshot 沪市 .SSE→.SHSE 归一。

- **expand 型**：只加列 + 数据归一，无破坏性；`CHECK (symbol NOT LIKE '%.SSE')` 归 **0182
  contract**（单独一次 release，前置=所有 live-task 已用 175b 代码重启 + 0 行 `%.SSE`）。
- 去重：唯一键 `(account_id,symbol,direction)`；若历史 `.SSE` 与 `.SHSE` 双行并存，直接
  UPDATE 会撞键 ⇒ 先按 `(account_id, replace(symbol,'.SSE','.SHSE'), direction)` 保 `ts` 最新
  删多余行，再 UPDATE。**纯 SQL 归一**（不调 Python `canonical`——后者对裸码抛错会让迁移 abort；
  非 `.SSE` 行原样保留，分组键对它们等于原值）。
- downgrade：仅 DROP COLUMN `mic_code`；symbol 数据**不回写**（`.SHSE` 无法区分源，避免误改
  本来就是 `.SHSE` 的单）——schema 可逆、数据部分为声明式 no-op。

Revision ID: 0181
Revises: 0180
"""
from alembic import op
import sqlalchemy as sa

revision = "0181"
down_revision = "0180"


def upgrade() -> None:
    # 1) security_master.mic_code（crypto/未知 exchange → NULL）
    op.add_column("security_master", sa.Column("mic_code", sa.Text(), nullable=True))
    op.execute(
        "UPDATE security_master SET mic_code = CASE exchange "
        "WHEN 'SHSE' THEN 'XSHG' WHEN 'SZSE' THEN 'XSHE' WHEN 'BSE' THEN 'BJSE' "
        "ELSE NULL END"
    )
    # 2) position_snapshot 去重：同一 (account, 归一symbol, direction) 只留 ts 最新
    op.execute(
        """
        WITH ranked AS (
            SELECT ctid,
                   row_number() OVER (
                       PARTITION BY account_id, replace(symbol, '.SSE', '.SHSE'), direction
                       ORDER BY ts DESC NULLS LAST, ctid DESC
                   ) AS rn
            FROM position_snapshot
        )
        DELETE FROM position_snapshot p USING ranked r
        WHERE p.ctid = r.ctid AND r.rn > 1
        """
    )
    # 3) 归一 .SSE → .SHSE
    op.execute(
        "UPDATE position_snapshot SET symbol = replace(symbol, '.SSE', '.SHSE') "
        "WHERE symbol LIKE '%.SSE'"
    )


def downgrade() -> None:
    # 数据不回写（见 docstring）：只回滚 schema
    op.drop_column("security_master", "mic_code")
