"""批 62a：signal_log/backtest_runs 加血缘三列（A03 §15.4 监管链——这笔单依据哪个源哪版数据）。

- source：实盘=接口行 provider（cfg.adapter）；回测=DataBus 帧行级 source distinct 聚合（逗号 join）
- fetched_at：**双语义列注释立法（v3.1）**——实盘=流到端时刻（hub pub_ts）；回测=读取时刻（DataBus 读侧；
  真实摄取时刻=sync 水位，另批）。报表侧命名「读取时刻」防审计误读
- dataset_version：TEXT 命名空间编码——实盘=`hub:{account_id}:{gen}`（流世代，per-account 纪元）；
  回测=`store:{kind}:{version}`（version=SELECT max(dataset_version) 该 kind bar 表——不依赖未 seed 的
  system_config 键）。bar 表侧 bigint 列不动，无类型混用点
存量 NULL=历史无血缘（正常，不回填）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0109"
down_revision = "0108"


def upgrade() -> None:
    op.add_column("signal_log", sa.Column("source", sa.Text(), nullable=True,
                  comment="数据源：实盘=接口行 provider；回测路径不写本表"))
    op.add_column("signal_log", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True,
                  comment="实盘=流到端时刻（hub pub_ts）——决策时数据新鲜度锚；bar 时刻由 order_log.bar_fingerprint 承载"))
    op.add_column("signal_log", sa.Column("dataset_version", sa.Text(), nullable=True,
                  comment="命名空间编码：hub:{account_id}:{gen}（流世代）"))
    op.add_column("backtest_runs", sa.Column("source", sa.Text(), nullable=True,
                  comment="DataBus 帧行级 source distinct 聚合（逗号 join；per-symbol 任务 UPDATE 合并去重）"))
    op.add_column("backtest_runs", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True,
                  comment="回测=读取时刻（DataBus 读侧，GREATEST 合并）；真实摄取时刻=sync 水位另批"))
    op.add_column("backtest_runs", sa.Column("dataset_version", sa.Text(), nullable=True,
                  comment="命名空间编码：store:{kind}:{version}（version=该 kind bar 表 max(dataset_version)）"))


def downgrade() -> None:
    op.drop_column("backtest_runs", "dataset_version")
    op.drop_column("backtest_runs", "fetched_at")
    op.drop_column("backtest_runs", "source")
    op.drop_column("signal_log", "dataset_version")
    op.drop_column("signal_log", "fetched_at")
    op.drop_column("signal_log", "source")
