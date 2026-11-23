"""批189：订单事件账 order_event（异步链路可解释）。

append-only：每次状态/阶段变化一条，order_log 只作投影。阶段 1 不改既有状态机行为。

Revision ID: 0195
Revises: 0194
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0195"
down_revision: Union[str, Sequence[str], None] = "0194"
branch_labels: Union[str, Sequence[str], str, None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS order_event (
          id bigserial PRIMARY KEY,
          order_id bigint REFERENCES order_log(id) ON DELETE SET NULL,
          client_order_id text,
          req_id text,
          ts timestamptz NOT NULL DEFAULT now(),
          stage text NOT NULL,
          status text,
          source text NOT NULL,
          detail jsonb,
          error text
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_order_event_order ON order_event (order_id, ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_order_event_req ON order_event (req_id, ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_order_event_cid ON order_event (client_order_id, ts)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS order_event")
