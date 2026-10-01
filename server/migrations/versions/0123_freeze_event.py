"""批 76 F1：冻结事件事实表 `freeze_event`（+ `web_base_url` 配置键种子）。

**为什么建表**（2026-09-28 用户框架裁定）：解决问题的是①告警与用户响应②架构容错自恢复
——表只是记录；但「冻结绝不频繁、记录有价值」⇒ 事实进 PG（介质立法：事实用 PG，
状态/瞬时用 Valkey）。收编此前只散落在 journalctl 的冻结史：过去要事后考古才能回答
「这个任务上周冻过几次、谁解的、用什么方式解的」。

**行语义**：一行 = 一次冻结事件。冻结发生即 INSERT（`unfrozen_at` NULL=进行中）；
解冻时 UPDATE（`unfrozen_at`/`unfreeze_method`/`operator`）。判定进行中= `unfrozen_at IS NULL`。

**三类冻结**（与 `strategy_runner/hub_worker.py` 三 sticky 冻结点一一对应，CHECK 锁枚举）：
- `ts_gap`：源侧丢根（水位后缺口 >60s，段首豁免不覆盖）——人工解（带洞窗须显式接受）；
- `seq_gap`：流序号跳变——rewarm 已补历史 ⇒ **自动解**（衔接判定通过即清 sticky）；
- `untrusted`：断线跨分钟失真 bar——人工解（数据污染事实不自动翻案）。

**解冻方式四值**（CHECK 锁）：`restart`（进程重启——启动时对 tid 的进行中事件补记闭环）/
`auto_reconnect`（F2 衔接判定自动解）/ `manual_web` / `manual_im`（人工解冻两入口）。

**guard 律**：`detail` 是 jsonb ⇒ 必须带类型守卫（0121 全库 jsonb 守卫律 +
`tests/test_jsonb_columns_guarded.py` 枚举闸），故 CHECK `jsonb_typeof(detail)='object'`。

`web_base_url` 种子（空串）：IM 发起的解冻走「Web 确认链」，深链需要 Web 外部基址；
空=未配置 ⇒ IM 回执降级提示「请到 Web 任务页解冻」，不阻断（诚实降级）。

Revision ID: 0123
Revises: 0122
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0123"
down_revision = "0122"


def upgrade() -> None:
    op.create_table(
        "freeze_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=True),      # hub per-account 流键真源；未知可空
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("freeze_type", sa.Text(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("watermark", sa.Text(), nullable=True),             # 冻结时水位（max_ts epoch 键）
        sa.Column("gap_target_ts", sa.Text(), nullable=True),         # 触发冻结的 bar ts（epoch 键）
        sa.Column("detail", postgresql.JSONB(), nullable=True),       # 上下文（gap 秒数等）
        sa.Column("unfrozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unfreeze_method", sa.Text(), nullable=True),
        sa.Column("operator", sa.Text(), nullable=True),              # 人工解冻的操作者；自动/重启留方法名
        sa.CheckConstraint("freeze_type IN ('ts_gap','seq_gap','untrusted')",
                           name="ck_freeze_event_type"),
        sa.CheckConstraint("unfreeze_method IS NULL OR unfreeze_method IN "
                           "('restart','auto_reconnect','manual_web','manual_im')",
                           name="ck_freeze_event_method"),
        # 闭环一致性：解冻了就必须记方法（防「解了但不知道谁解的」半记录）
        sa.CheckConstraint("unfrozen_at IS NULL OR unfreeze_method IS NOT NULL",
                           name="ck_freeze_event_close_consistent"),
        # 0121 全库 jsonb 守卫律（jsonb 列必须带类型守卫，禁双编码降级）
        sa.CheckConstraint("detail IS NULL OR jsonb_typeof(detail) = 'object'",
                           name="ck_freeze_event_detail_jsonb"),
    )
    # 进行中事件查询（任务页/运维：该任务冻没冻）+ 时间线（历史回看）
    op.execute("CREATE INDEX ix_freeze_event_open ON freeze_event (task_id) "
               "WHERE unfrozen_at IS NULL")
    op.create_index("ix_freeze_event_task_frozen_at", "freeze_event", ["task_id", "frozen_at"])

    # web_base_url 配置键种子（幂等：已存在则不动——不覆盖运维已填的值）
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) VALUES "
        "('web_base_url', '', 'text', "
        "'Web 外部基址（如 https://quant.example.com）——IM 冻结解冻确认链的深链前缀；"
        "空=未配置，IM 回执降级提示到 Web 手工解冻') "
        "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.drop_table("freeze_event")
    # ⚠ 键值一并回收（运维若已填值则随之丢失——降级即放弃本批 IM 解冻深链能力，显式接受）
    op.execute("DELETE FROM system_config WHERE key = 'web_base_url'")
