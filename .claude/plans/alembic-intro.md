# 引入 Alembic schema 版本管理（对齐 safebox，根治 schema 漂移）

## 背景
当前 quant 用 `CREATE TABLE IF NOT EXISTS` 分散各 handler + `init-schema.sql` 集中建表。问题：无版本管理、schema 变更靠手动 ALTER（audit_log 加 old_value 时 DDL 漂移）、加列不自动应用。这次部署踩了 6 个 schema 坑。

safebox 用 alembic：迁移文件链 + `alembic upgrade head` 幂等升级 + 可回滚。quant 对齐此机制。

## quant vs safebox 差异

| 项 | safebox | quant |
|---|---|---|
| DB 驱动 | asyncpg（异步） | psycopg3（同步） |
| SQLAlchemy 模型 | 有（autogenerate） | **无**（手写迁移） |
| URL 来源 | app.config.settings | .env 的 QUANT_DB_URL |

## 方案

### 1. 依赖
`server/requirements.txt` 加：
```
alembic>=1.13
sqlalchemy>=2.0      # alembic 依赖，显式声明
```

### 2. 结构
```
server/
├── alembic.ini                    # script_location=migrations, URL 占位（env.py 覆盖）
└── migrations/
    ├── env.py                     # 同步 psycopg3，从 QUANT_DB_URL 读，无 target_metadata
    ├── script.py.mako             # alembic 迁移模板（标准）
    └── versions/
        └── 0001_initial.py        # 初始迁移：10 张表 op.create_table
```

### 3. `migrations/env.py`（quant 版，同步 psycopg3 + 无 autogenerate）
- `load_dotenv()` 读 .env
- `QUANT_DB_URL` 转成 `postgresql+psycopg://`（sqlalchemy 用 psycopg3）
- **不设 target_metadata**（quant 无 SQLAlchemy 模型，手写迁移，不用 autogenerate）
- 同步 `create_engine` + `run_migrations_online`

### 4. `versions/0001_initial.py`（初始迁移）
- `down_revision = None`（起点）
- `upgrade()`：10 张表 `op.create_table` + `op.create_index`（从 init-schema.sql 转）
- `downgrade()`：`op.drop_table` 反序删
- **不用 ALTER OWNER**：alembic 用 quant 用户跑（QUANT_DB_URL），建的表 owner 自动 = quant（解决之前 owner=postgres 问题）

### 5. `alembic.ini`
- `script_location = migrations`
- `sqlalchemy.url =`（留空，env.py 从 .env 覆盖）
- 标准日志配置

### 6. `quant-deploy.sh` 加 `migrate` 动作
- `migrate`：`sudo -u quant bash -c 'cd PROJECT_PATH && venv/bin/alembic upgrade head'`
- 用 quant 用户跑（不是 postgres），owner 自动 quant
- 首次部署：`deploy -> migrate -> init-seed -> restart`
- `init-schema` 动作保留（备用，alembic 是主）

### 7. DEPLOY.md 更新
- 首次部署 §2.3：`init-schema` -> `migrate`（alembic upgrade head）
- 后续 schema 变更流程：`alembic revision -m "add xxx"` + 手写 upgrade/downgrade + `./scripts/deploy-server.sh migrate`

### 8. 各 handler CREATE TABLE IF NOT EXISTS
- **保留**（运行时兜底，alembic 是主）
- 不冲突：alembic 建表后，handler 的 IF NOT EXISTS 跳过

## 后续 schema 变更流程（alembic 上手后）

```bash
# 1. 生成空迁移文件
cd server && venv/bin/alembic revision -m "add col xxx to yyy"
# 2. 手写 versions/xxxx_add_col_xxx.py 的 upgrade()/downgrade()
# 3. 部署
./scripts/deploy-server.sh migrate   # alembic upgrade head
```

> quant 无 SQLAlchemy 模型，不能 `--autogenerate`，手写 op.add_column/op.drop_column。

## 落地清单

| 文件 | 改动 |
|---|---|
| `server/requirements.txt` | 加 alembic + sqlalchemy |
| `server/alembic.ini` | 新建 |
| `server/migrations/env.py` | 新建（同步 psycopg3 + 无 autogenerate） |
| `server/migrations/script.py.mako` | 新建（alembic 标准模板） |
| `server/migrations/versions/0001_initial.py` | 新建（10 表 op.create_table） |
| `scripts/quant-deploy.sh` | 加 `migrate` 动作 |
| `DEPLOY.md` | §2.3 init-schema->migrate + 后续变更流程 |

## 验证

1. 本地 `cd server && venv/bin/alembic upgrade head` -> 建所有表（本地 quant 库已有表，alembic_version 记录版本）
2. `alembic current` -> 显示 0001
3. `alembic downgrade -1` -> 回退（drop 所有表）
4. `alembic upgrade head` -> 重建
5. 本地后端启动 + health OK

## 本棒范围与不做

**做**：alembic 引入（依赖 + 配置 + initial 迁移 + migrate 动作 + DEPLOY.md）。

**不做**：
- 各 handler 的 CREATE TABLE IF NOT EXISTS 不移除（保留兜底）
- init-schema.sql 保留（备用）
- autogenerate（quant 无 SQLAlchemy 模型）
- 服务器实际部署（用户照 DEPLOY.md）
