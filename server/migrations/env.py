"""Alembic 迁移环境配置（quant，同步 psycopg3，无 SQLAlchemy 模型，手写迁移）。

quant 用 psycopg（非 asyncpg），无 SQLAlchemy ORM 模型，所以：
- 同步 create_engine（不是 async）
- 不设 target_metadata（不能 --autogenerate，手写迁移）
- URL 从 .env 的 QUANT_DB_URL 读，转 postgresql+psycopg:// 给 sqlalchemy
"""
from logging.config import fileConfig
import os
import sys

# 双盲 B-S1（2026-08-21，部署阻断）：0051 起迁移 import 应用代码（quant_common.crypto）——
# 服务器 console script（venv/bin/alembic）的 sys.path[0]=venv/bin 不含项目根，
# 本地 python -m alembic（cwd 入 path）掩盖了此坑。显式插入 server 根。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    """在线模式：连接数据库并执行迁移。

    DB 优化防线（2026-08-21，锁链事件根治——18 号文档 §1.7）：alembic DDL 带
    lock_timeout=15s——被长事务堵时快速失败（报锁冲突）而非无限排队闷死部署管道。
    注意 CREATE INDEX 仍非 CONCURRENTLY（存量 50 迁移历史惯性；新索引走 CONCURRENTLY
    的约定记 18 号 §2 规范）。
    """
    import os
    stmt_ms = int(os.environ.get("QUANT_DB_STMT_TIMEOUT_MS", "3600000"))   # 迁移本身放宽 1h
    # 兼容外部 PGOPTIONS（期望基线生成流程用它切 scratch schema——connect_args 会覆盖
    # libpq 环境变量，此处透传保生成链可用）
    import shlex as _shlex
    _ext_opts = os.environ.get("PGOPTIONS", "")
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
        connect_args={"options": f"-c statement_timeout={stmt_ms} -c lock_timeout=15000 {_ext_opts}"},
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
