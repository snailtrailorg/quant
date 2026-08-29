"""risk_log 决策日志 + reconcile_issue 对账差异单（P1-1/P1-2，web-design 05 §5.3/§5.4）。

- risk_log：风控决策审计（approve/reject/adjust 三类可筛）——风控页最有审计价值的数据
  （06 B#2），check_order 出口统一写入。
- reconcile_issue：三账对账差异单结构化+处置状态持久化（前端存储会跨设备失同步、
  与通知中心脱节——05 §5.4 要点 1）。旧 issues 字符串数组兼容期双写（勿误修#11）。

Revision ID: 0055
Revises: 0054
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0055"
down_revision: str = "0054"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS risk_log (
        id BIGSERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL DEFAULT now(),
        action TEXT NOT NULL,            -- approve(放行)/reject(拒单)/adjust(覆写截断)
        symbol TEXT,
        rule TEXT,                       -- 命中规则键（如 global.single_position_pct）
        detail TEXT,                     -- 人读明细（含调整前后量）
        severity TEXT NOT NULL DEFAULT 'info',
        username TEXT
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_risk_log_ts ON risk_log (ts DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_risk_log_action ON risk_log (action)")
    op.execute("""
    CREATE TABLE IF NOT EXISTS reconcile_issue (
        id BIGSERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        issue_type TEXT NOT NULL,        -- position_diff / signal_no_order / order_no_trade / slip_anomaly
        detail TEXT,
        broker_qty NUMERIC,              -- 结构化（position_diff 专属，可空）
        derived_qty NUMERIC,
        status TEXT NOT NULL DEFAULT 'open',   -- open/verified/ignored/exempt
        first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        handled_by TEXT,
        note TEXT,
        exempt_qty NUMERIC,              -- 豁免基准量：|diff| 超此量重新告警
        exempt_until DATE
    )""")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_reconcile_issue_open "
        "ON reconcile_issue (symbol, issue_type) WHERE status = 'open'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reconcile_issue")
    op.execute("DROP TABLE IF EXISTS risk_log")
