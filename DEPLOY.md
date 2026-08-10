# 多市场混合量化交易平台 · 生产部署指南

> 部署到与 SafeBox **同一服务器**，严格隔离。新服务器迁移/全新部署均用本指南。
> 目标 OS：Alibaba Cloud Linux 3 | 部署路径：`/data/websites/snailtrail.org/quant/` | 域名：`quant.snailtrail.org`

## 目标架构

```
Internet -> Apache httpd (TLS + 反代)
            ├─ quant.snailtrail.org:443
            │   ├─ /api/*  -> Uvicorn (127.0.0.1:8001) -> FastAPI ─┐
            │   └─ /*      -> 静态文件 (web/dist)                    │
            └─ safebox.snailtrail.org（互不干扰）                     │
                                                                     ├─ PostgreSQL (quant 库, md5)
                                                                     └─ Redis6 (db4=VALKEY, db5=CELERY_BROKER, db6=CELERY_RESULT)
```

## 隔离原则（与 SafeBox 共存，铁律）

| 资源 | SafeBox | Quant | 隔离方式 |
|---|---|---|---|
| PG 库 | `safebox` | `quant` | 不同库名 |
| Redis DB | db0 | **db4(VALKEY) + db5(BROKER) + db6(RESULT)** | 不同 db 号 |
| 后端端口 | :8000 | **:8001** | 不同端口 |
| Apache vhost | safebox.snailtrail.org | quant.snailtrail.org | 不同 ServerName |
| 系统用户 | safebox | quant | 不同用户 |

**铁律**：`clear-redis` 只 `FLUSHDB -n <db>`，**绝不用 `FLUSHALL`**；`restart-web` 用 `reload`。

---

## 一、新服务器初始化（一次性）

### 1.1 装系统依赖

```bash
sudo dnf install -y postgresql15-server redis6 httpd python3.11 python3.11-pip certbot python3-certbot-apache
```

### 1.2 初始化 PostgreSQL（数据目录自定义 + md5 认证）

```bash
sudo postgresql-setup --initdb
sudo systemctl stop postgresql
sudo mv /var/lib/pgsql/data /data/databases/pgsql
sudo mkdir -p /etc/systemd/system/postgresql.service.d
sudo tee /etc/systemd/system/postgresql.service.d/override.conf <<'EOF'
[Service]
Environment=PGDATA=/data/databases/pgsql
EOF
sudo systemctl daemon-reload
# pg_hba 改 md5（密码认证）
sudo sed -i 's/^local\s\+all\s\+all\s\+peer/local   all             all             md5/' /data/databases/pgsql/pg_hba.conf
sudo sed -i 's/^host\s\+all\s\+all\s\+127.0.0.1\/32\s\+ident/host    all             all             127.0.0.1\/32    scram-sha-256/' /data/databases/pgsql/pg_hba.conf
sudo systemctl enable --now postgresql
```

### 1.3 创建 quant 数据库 + 角色（md5 密码）

```bash
sudo -u postgres psql <<SQL
CREATE USER quant WITH PASSWORD '$(openssl rand -hex 16)';  -- 记下密码！
CREATE DATABASE quant OWNER quant;
GRANT ALL ON DATABASE quant TO quant;
\c quant
GRANT ALL ON SCHEMA public TO quant;
SQL
```

### 1.4 启动 Redis6

```bash
sudo sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis6/redis6.conf
sudo systemctl enable --now redis6
```

### 1.5 创建系统用户和目录

```bash
sudo useradd -m safebox
sudo useradd -m quant
sudo mkdir -p /data/websites/snailtrail.org/{safebox,quant}/{server,web} /var/log/{safebox,quant}
sudo chown -R safebox:safebox /data/websites/snailtrail.org/safebox /var/log/safebox
sudo chown -R quant:quant /data/websites/snailtrail.org/quant /var/log/quant
sudo usermod -a -G quant apache
sudo usermod -a -G safebox apache
sudo chmod 750 /data/websites/snailtrail.org/{safebox,quant}{,/web}
```

### 1.6 安全模型：michael 用户 + sudoers（关键）

`michael` 是唯一能 ssh 服务器 + sudo 的用户。`bernard` 只能通过 michael 跑两个固定部署脚本：

```bash
# 1. 确保 michael 用户存在 + ssh key 配好
sudo useradd -m michael
sudo -u michael mkdir -p /home/michael/.local/bin
# michael 的 ssh authorized_keys 配好（开发机 michael key）

# 2. sudoers：bernard 只能 sudo -u michael 跑两个脚本，免密
sudo tee /etc/sudoers.d/snailtrailorg-deploy <<'EOF'
bernard ALL=(michael) NOPASSWD: /home/michael/.local/bin/safebox-deploy.sh
bernard ALL=(michael) NOPASSWD: /home/michael/.local/bin/quant-deploy.sh
EOF
sudo chmod 440 /etc/sudoers.d/snailtrailorg-deploy
sudo visudo -c  # 校验语法

# 3. 装 quant-deploy.sh（safebox-deploy.sh 同理从 safebox 仓库）
# 从 quant 仓库 scripts/quant-deploy.sh 复制
sudo -u michael cp /path/to/quant-deploy.sh /home/michael/.local/bin/quant-deploy.sh
sudo -u michael chmod +x /home/michael/.local/bin/quant-deploy.sh
```

> bernard 在开发机跑 `./scripts/deploy-server.sh`，内部 `sudo -u michael /home/michael/.local/bin/quant-deploy.sh ...`，michael ssh 到服务器 + sudo 操作。bernard 不直接碰服务器。

---

## 二、Quant 首次部署

### 2.1 部署代码 + venv

```bash
# 开发机
cd ~/Projects/quant
rsync -avz --delete --exclude='__pycache__/' --exclude='*.pyc' \
    --exclude='venv/' --exclude='.env' --exclude='.pytest_cache/' \
    server/ michael@snailtrail.org:~/quant-server/

# 服务器
ssh michael@snailtrail.org
sudo cp -r ~/quant-server/* /data/websites/snailtrail.org/quant/server/
sudo chown -R quant:quant /data/websites/snailtrail.org/quant/server/
cd /data/websites/snailtrail.org/quant/server
sudo -u quant python3.11 -m venv venv
sudo -u quant bash -c 'source venv/bin/activate && pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt'
```

### 2.2 生成 .env（init-env.sh 交互式，避免手动 sed）

```bash
sudo -u quant bash scripts/init-env.sh
# 提示填：quant 数据库密码、Tushare token、DeepSeek key（可空）、全量起点（默认 20100101）
# 生成 .env（chmod 600，db4/5/6 隔离 + SYNC_START_DATE）
```

### 2.3 Schema 迁移（alembic upgrade head，对齐 safebox）

```bash
# 用 quant 用户跑（owner 自动 quant，无需 ALTER OWNER）
sudo -u quant bash -c 'cd /data/websites/snailtrail.org/quant/server && venv/bin/alembic upgrade head'
# 建所有表（users/audit_log/sync_config/sync_log/bar_1D/daily_basic/asset_static_info/cb_basic_info/etf_basic_info/trade_cal）+ alembic_version 版本记录
```

> alembic 是 schema 版本管理主工具。`init-schema.sql` 保留作备用（postgres 用户跑）。后续 schema 变更走 alembic 迁移文件，不手动 ALTER。

**后续 schema 变更流程**（加列/加表/加索引）：
```bash
# 1. 生成空迁移文件
cd server && venv/bin/alembic revision -m "add col xxx to yyy"
# 2. 手写 versions/xxxx_add_col_xxx.py 的 upgrade()/downgrade()（quant 无 SQLAlchemy 模型，手写不用 autogenerate）
# 3. 部署
./scripts/deploy-server.sh migrate   # alembic upgrade head 自动应用
```

### 2.4 插 sync_config 种子

```bash
sudo -u quant psql -d quant -f scripts/init-seed.sql
# 8 条同步任务配置（astock_daily 等）
```

### 2.5 systemd 3 服务

创建 `/etc/systemd/system/quant-web-api@quant.service`、`quant-celery-worker@quant.service`、`quant-celery-beat@quant.service`（内容见 `server/scripts/systemd/`，端口 :8001，User=quant，After=redis6.service）。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now quant-web-api@quant quant-celery-worker@quant quant-celery-beat@quant
curl http://127.0.0.1:8001/health    # {"status":"ok"}
```

> web-api 启动时自动建默认 admin（admin/admin123，登录后改密码）。

### 2.6 Apache vhost + HTTPS

`/etc/httpd/conf.d/quant.conf`（ProxyPass /api/ -> :8001，DocumentRoot web，FallbackResource /index.html，**注释独占一行不写行末**）：

```bash
sudo httpd -t && sudo systemctl reload httpd
sudo certbot --apache -d quant.snailtrail.org
```

### 2.7 部署前端

```bash
# 开发机
cd ~/Projects/quant/web && npm run build
rsync -avz --delete dist/ michael@snailtrail.org:~/quant-web/
ssh michael@snailtrail.org "sudo cp -r ~/quant-web/* /data/websites/snailtrail.org/quant/web/ && sudo chown -R quant:quant /data/websites/snailtrail.org/quant/web"
```

### 2.8 首次全量数据同步

登录 `https://quant.snailtrail.org`（admin/admin123）-> 数据同步页 -> astock_daily "管理标的" -> "全量同步全部"（Celery 后台，5534 只从 2010 起，约 37 分钟，3GB）。

---

## 三、日常部署（便捷脚本）

### 3.1 安装 deploy 脚本（一次性，开发机 bernard）

```bash
sudo -u michael cp ~/Projects/quant/scripts/quant-deploy.sh /home/michael/.local/bin/
sudo -u michael chmod +x /home/michael/.local/bin/quant-deploy.sh
```

### 3.2 日常更新

```bash
# 开发机（bernard）
cd ~/Projects/quant
./scripts/deploy-server.sh   # 部署 server/ + 重启 web-api + celery
./scripts/deploy-web.sh      # npm build + 部署 dist + reload httpd
```

### 3.3 首次部署用 quant-deploy.sh 一条命令

```bash
sudo -u michael /home/michael/.local/bin/quant-deploy.sh --server snailtrail.org \
    deploy ~/Projects/quant/server /data/websites/snailtrail.org/quant/server \
    --exclude .env --exclude venv/ \
    init-schema migrate init-seed restart-server restart-celery
```

---

## 四、SafeBox 改进（避免破坏 Quant）

SafeBox 的 `safebox-deploy.sh` 有两处需改（在 safebox 仓库改）：

1. **`clear_redis`**：`FLUSHALL` -> `FLUSHDB -n 0`（只清 safebox db0，不碰 quant db4/5/6）
2. **`restart_web`**：`systemctl restart httpd` -> `systemctl reload httpd`（不断连接）

改后重新 `cp` 到 `/home/michael/.local/bin/safebox-deploy.sh`。

---

## 五、迁移步骤（新服务器）

1. **新服务器初始化**（§1，含 sudoers 安全模型）
2. **装两个 deploy 脚本**到 `/home/michael/.local/bin/`
3. **SafeBox 部署**（用 safebox DEPLOY.md 全新部署，数据重新初始化）
4. **Quant 部署**（§2：deploy + venv + init-env + migrate + init-seed + systemd + Apache + 全量）
5. **DNS 切换**：quant.snailtrail.org + safebox.snailtrail.org 指向新服务器

---

## 六、磁盘规划

| 项 | 大小 |
|---|---|
| bar_1D 全量（2010-今，5534只×3950天≈2186万行） | ~3GB |
| daily_basic 全量 | ~3GB |
| safebox 库 | <1GB |
| PG 日志 + 备份 | ~5GB |
| **建议新服务器磁盘** | **40GB+** |

> 若磁盘紧张，`.env` 改 `SYNC_START_DATE=20200101`（近6年，bar_1D 约 0.8GB）。

---

## 七、运维排查

```bash
# 日志
ssh michael@snailtrail.org 'sudo journalctl -u quant-web-api@quant -n 50 --no-pager'
ssh michael@snailtrail.org 'sudo journalctl -u quant-celery-worker@quant -n 50'
ssh michael@snailtrail.org 'sudo tail -f /var/log/quant/error.log'

# 同步进度
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s "http://127.0.0.1:8001/api/sync/all/astock_daily/progress" -H "Authorization: Bearer $TOKEN"

# 数据库
sudo -u postgres psql -d quant -c "SELECT id, last_status, last_sync_count FROM sync_config;"
```

## 八、常见问题

| 症状 | 原因 | 解决 |
|---|---|---|
| psql `password authentication failed` | quant 角色没设密码 | `ALTER USER quant WITH PASSWORD '...'`，.env 的 QUANT_DB_URL 带密码 |
| 登录失败 `column "old_value" does not exist` | audit_log 表旧 schema | 跑 `init-schema.sql`（建表含 old_value/new_value） |
| 同步失败 `relation "bar_1d" does not exist` | bar_1D 表没建 | 跑 `init-schema.sql`（集中建表） |
| 同步失败 `must be owner of table` | 表 owner 不是 quant | `init-schema.sql` 的 `ALTER TABLE OWNER TO quant` |
| celery 报 `asset_static_info does not exist` | astock_list 没同步 | 先 `sync('astock_list')` 建表 + 填股票列表 |
| Apache `subrequest nesting levels` | FallbackResource 递归 | web 目录空（前端没部署）或改 mod_rewrite |
| Apache `Invalid ProxyPass parameter` | 行内 `#` 注释 | 注释独占一行，不写行末 |
| 磁盘满 | bar_1D 全量 3GB | `.env` 改 `SYNC_START_DATE=20200101` |
| DataManage 看不到全量进度 | last_status 不反映 Celery | 已修（sync_all_symbols 更新 last_status） |
