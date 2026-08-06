"""Alembic 迁移环境配置（quant，同步 psycopg3，无 SQLAlchemy 模型，手写迁移）。

quant 用 psycopg（非 asyncpg），无 SQLAlchemy ORM 模型，所以：
- 同步 create_engine（不是 async）
- 不设 target_metadata（不能 --autogenerate，手写迁移）
- URL 从 .env 的 QUANT_DB_URL 读，转 postgresql+psycopg:// 给 sqlalchemy
"""
from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import pool, create_engine
from dotenv import load_dotenv

load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 从 .env 读 QUANT_DB_URL，转 sqlalchemy + psycopg3 格式
db_url = os.environ.get("QUANT_DB_URL", "postgresql://quant@127.0.0.1:5432/quant")
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", db_url)

# quant 无 SQLAlchemy 模型，不设 target_metadata（手写迁移，不用 autogenerate）
target_metadata = None


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 但不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并执行迁移。"""
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
