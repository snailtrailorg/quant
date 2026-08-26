"""沙箱 alembic 环境（真 alembic + 本地 PG quant 库 sbx_deploy schema）。

与生产 env.py 同形约束：连接串来自环境（QUANT_ALEMBIC_URL/QUANT_ALEMBIC_SCHEMA，
由 quant-alembic-wrapper source shared/.env 注入——.env 属 quant 600 的隔离设计）。
沙箱场景 2（坏迁移）靠 release.yml 阶段 4 的 DDL 门在实跑前拦截。
"""
import os

from alembic import context

config = context.config

URL = os.environ.get("QUANT_ALEMBIC_URL", "postgresql+psycopg://quant@127.0.0.1:5432/quant")
SCHEMA = os.environ.get("QUANT_ALEMBIC_SCHEMA", "sbx_deploy")


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(
        URL,
        future=True,
        # 未限定 schema 的 DDL 一律落沙箱 schema（不污染 dev 库其他 schema）
        connect_args={"options": f"-csearch_path={SCHEMA}"},
    )
    with engine.connect() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
        conn.commit()
    with engine.begin() as conn:
        context.configure(
            connection=conn,
            version_table_schema=SCHEMA,
            compare_type=True,
        )
        context.run_migrations()


run_migrations_online()
