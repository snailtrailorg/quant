# EXPAND-CONTRACT: phase=expand
"""批 120：安全态势页线①——`security_snapshot`（每日快照）+ `security_finding`（漏洞/偏差行）。

任务＝`flow/任务/批120-安全态势页.md`（2026-10-11 手工安全评估转持续自动化的后端线）。

**security_snapshot**（一天一行，scan_date unique）：六分区 jsonb 快照
（components/ports/ssh_cfg/dnf/headers——采集器单分区失败只标该分区 status=degraded，
绝不静默空表＝假绿红线，同判返工裁定 3）+ ai_summary（LLM 每日摘要，fail-soft 可空）。

**security_finding**（漏洞/偏差行，跨日累积）：source∈{pip-audit,dnf,baseline}（写入侧
代码单源，DB 不加域 CHECK——词表在代码=避免第二真源，同 0144 needs 立法）；唯一键
(source,pkg,vuln_id)——first_seen 当日 ⇒ 告警一次（去重），已修复项 last_seen 停更自然沉底。

**写法（仓例）**：全部 `op.execute(<内联字面量>)`（`--sql` 离线渲染安全，同 0132~0144）；
`CREATE TABLE IF NOT EXISTS` + `DROP ... IF EXISTS`（复跑/回滚安全）。
jsonb 列按 jsonb 守卫闸门（tests/test_jsonb_columns_guarded.py）加**形状律** object CHECK。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0145"
down_revision: Union[str, Sequence[str], None] = "0144"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# jsonb 形状律（仓例 0120/0144）：非 NULL 必须 object——拦双重编码静默降级
_SHAPE_CHK = (
    "ALTER TABLE security_snapshot ADD CONSTRAINT {name} "
    "CHECK ({col} IS NULL OR jsonb_typeof({col}) = 'object')"
)
_SHAPE_COLS = {
    "components": "security_snapshot_components_shape_chk",
    "ports": "security_snapshot_ports_shape_chk",
    "ssh_cfg": "security_snapshot_ssh_cfg_shape_chk",
    "dnf": "security_snapshot_dnf_shape_chk",
    "headers": "security_snapshot_headers_shape_chk",
}


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS security_snapshot (
            id          bigserial PRIMARY KEY,
            scan_date   date NOT NULL,
            status      text NOT NULL DEFAULT 'ok',
            components  jsonb,
            ports       jsonb,
            ssh_cfg     jsonb,
            dnf         jsonb,
            headers     jsonb,
            ai_summary  text,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_security_snapshot_date UNIQUE (scan_date)
        )
    """)
    for col, name in _SHAPE_COLS.items():
        op.execute(f"ALTER TABLE security_snapshot DROP CONSTRAINT IF EXISTS {name}")
        op.execute(_SHAPE_CHK.format(name=name, col=col))

    op.execute("""
        CREATE TABLE IF NOT EXISTS security_finding (
            id           bigserial PRIMARY KEY,
            source       text NOT NULL,
            pkg          text NOT NULL,
            vuln_id      text NOT NULL,
            fix_version  text,
            first_seen   date NOT NULL,
            last_seen    date NOT NULL,
            CONSTRAINT uq_security_finding UNIQUE (source, pkg, vuln_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_security_finding_source_pkg "
        "ON security_finding (source, pkg)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS security_finding")
    op.execute("DROP TABLE IF EXISTS security_snapshot")
