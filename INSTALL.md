# 安装指南（组件级手动装配）

> **读者定位（2026-08-28 明确）**：本指南是**组件级手动装配手册**——写给需要理解系统怎么拼起来、
> 做排障或最小试用的开发者。**正式安装的唯一路径是 `deploy/` 工件化管道**（bootstrap 装机+
> releases 不可变工件+自动回滚，操作手册 `deploy/DEPLOY.md`）——不是"二选一"，是角色不同。
> 手动装配的布局与 systemd 单元 expectations 有差异（工件化 venv/.env 在 `shared/`）。
>
> **产品化重写待办**：本文件未来将重写为"独立实例部署者手册"（单一路径+前置条件+首次配置，
> 删手动装配）——触发点=批 6 收口/首个独立实例交付时（见 flow/待办.md 常规线）。

## 1. 环境要求

### 1.1 服务器规格

| 资源 | 最低 | 推荐 |
|---|---|---|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 20 GB | 40 GB+（历史数据约 3GB） |
| 操作系统 | 任意现代 Linux（RHEL/CentOS/Debian/Ubuntu 等） | RHEL 系（dnf/yum）或 Debian 系（apt） |

### 1.2 软件依赖

| 软件 | 版本要求 | 说明 |
|---|---|---|
| **Python** | 3.10 或 3.11（不支持 3.14，vnpy 兼容性） | 实盘交易需 vnpy_xtp 编译 |
| **PostgreSQL** | 15+（推荐 18） | 需 pgvector 扩展 |
| **Redis / Valkey** | 6.0+ | Valkey 是 Redis 协议兼容的开源替代 |
| **Node.js** | 18+ | 仅前端构建用，服务器可不需要 |
| **Web 服务器** | Nginx 或 Apache | 反代 FastAPI + 静态文件 |
| **C 编译器 + Python dev** | gcc / python3-dev | vnpy_xtp 编译需要 |

> **vnpy_xtp 注意**：如需 A 股/可转债/ETF 实盘交易（中泰 XTP），需编译 vnpy_xtp。纯 Python 依赖（FastAPI/Celery 等）在 Python 3.10-3.13 全部兼容，卡点仅在 vnpy 的 PySide6 pin。

---

## 2. 系统准备

### 2.1 安装系统包

**RHEL 系（Fedora / CentOS / Rocky / AlmaLinux / Alibaba Cloud Linux）：**
```bash
sudo dnf install -y postgresql-server postgresql-devel redis python3.11 python3.11-devel gcc gcc-c++ make
```

**Debian 系（Ubuntu / Debian）：**
```bash
sudo apt update && sudo apt install -y postgresql postgresql-server-dev-all redis-server python3.11 python3.11-dev gcc g++ make
```

### 2.2 启动 PostgreSQL

```bash
# RHEL 系
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql

# Debian 系通常自动初始化
sudo systemctl enable --now postgresql
```

确保 `pg_hba.conf` 允许本地密码连接（`md5` 或 `scram-sha-256`）。

### 2.3 创建数据库和用户

```bash
sudo -u postgres psql <<SQL
CREATE USER quant WITH PASSWORD '你的强密码';
CREATE DATABASE quant OWNER quant;
\c quant
CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector
SQL
```

### 2.4 启动 Redis

```bash
sudo systemctl enable --now redis      # 或 redis6 / valkey
# 验证
redis-cli ping                        # PONG
```

---

## 3. 部署后端

### 3.1 克隆代码

```bash
git clone <你的仓库地址> /opt/quant
cd /opt/quant/server
```

### 3.2 创建虚拟环境

```bash
python3.11 -m venv venv
source venv/bin/activate
# 注：工件化布局（deploy/ 管道）venv 在 shared/venv 并由 wrapper 以 quant 运行——
# 手动路径仅适用于不入 systemd 单元的裸跑/试用
```

### 3.3 安装依赖

```bash
pip install -r requirements.txt
```

> 如在中国大陆，可加镜像加速：`pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt`

### 3.4 配置环境变量

```bash
bash scripts/init-env.sh
```

交互式填写：
- PostgreSQL 连接地址和密码
- Redis/Valkey 地址
- Tushare 数据源 token
- AI 模型 API key（DeepSeek/火山方舟/GLM，可后配）
- 数据同步起始日期（默认全量，磁盘紧张改 `20200101`）

或手动创建 `.env` 文件（参照 `.env.example`）：

```ini
# 数据库
QUANT_DB_URL=postgresql://quant:密码@127.0.0.1:5432/quant

# Redis
VALKEY_URL=redis://127.0.0.1:6379/0

# 数据源
TUSHARE_TOKEN=你的tushare_token

# AI 模型（至少配一个）
DEEPSEEK_API_KEY=你的deepseek_key
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 根密钥（推荐，自动派生 JWT_SECRET + ENCRYPTION_KEY）
# 生成：python3 -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY=你的根密钥

# 实盘开关（生产环境设 false）
ENABLE_LIVE_TRADING=false
```

### 3.5 初始化数据库 Schema

```bash
cd /opt/quant/server
alembic upgrade head                          # 创建所有表
psql -d quant -f scripts/init-seed.sql       # 插入同步配置种子
```

### 3.6 编译 vnpy_xtp（仅实盘需要）

```bash
# 仅当需要 A 股/可转债/ETF 实盘交易时
# 需下载中泰 XTP SDK（.so 文件）放到 vendor/xtp/lib/
# 参考: https://xtp.zts.com.cn/service/download
PATH=venv/bin:$PATH CPATH=vendor/xtp/include LIBRARY_PATH=vendor/xtp/lib \
    pip install --no-build-isolation vnpy_xtp
```

---

## 4. 部署前端

### 4.1 构建

```bash
cd /opt/quant/web
npm install
npm run build    # 产物在 dist/
```

### 4.2 配置 Web 服务器

**Nginx 示例：**
```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # 前端静态文件
    root /opt/quant/web/dist;
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 反代
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

**Apache 示例：** 参见 `server/scripts/nginx/quant.conf`

---

## 5. 配置 systemd 服务

### 5.1 Web API

```bash
sudo tee /etc/systemd/system/quant-web-api.service > /dev/null <<'EOF'
[Unit]
Description=Quant Web API
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/quant/server
EnvironmentFile=/opt/quant/server/.env
ExecStart=/opt/quant/server/venv/bin/uvicorn src.web_api.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 5.2 Celery Worker + Beat

```bash
sudo tee /etc/systemd/system/quant-celery-worker.service > /dev/null <<'EOF'
[Unit]
Description=Quant Celery Worker
After=network.target redis.service

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/quant/server
EnvironmentFile=/opt/quant/server/.env
ExecStart=/opt/quant/server/venv/bin/celery -A src.scheduler.app worker -c 2 --loglevel=info
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/quant-celery-beat.service > /dev/null <<'EOF'
[Unit]
Description=Quant Celery Beat
After=redis.service

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/quant/server
EnvironmentFile=/opt/quant/server/.env
ExecStart=/opt/quant/server/venv/bin/celery -A src.scheduler.app beat --loglevel=info
Restart=always

[Install]
WantedBy=multi-user.target
EOF
```

### 5.3 策略实盘进程（模板，按需启用）

```bash
sudo tee /etc/systemd/system/quant-strategy@.service > /dev/null <<'EOF'
[Unit]
Description=Quant Strategy %i
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/quant/server
EnvironmentFile=/opt/quant/server/.env
Environment=QT_QPA_PLATFORM=offscreen
ExecStart=/opt/quant/server/venv/bin/python -m src.strategy_runner.main --id %i
Restart=always
RestartSec=10
MemoryMax=512M
CPUQuota=50%

[Install]
WantedBy=multi-user.target
EOF

# Web 控制 systemd（polkit 规则）
sudo tee /etc/polkit-1/rules.d/10-quant-strategy.rules > /dev/null <<'EOF'
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit").startsWith("quant-strategy@") &&
        subject.user == "quant") {
        return polkit.Result.YES;
    }
});
EOF
```

### 5.4 启动全部服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now quant-web-api quant-celery-worker quant-celery-beat

# 验证
curl http://127.0.0.1:8000/health    # {"status":"ok","version":"0.1.0"}
```

首次启动自动创建默认管理员（admin/admin123，**首次登录请改密码**）。

---

## 6. 飞书机器人（可选）

如需飞书移动干预（AI 查询 + 熔断确认）：

1. 登录 Web -> 系统设置 -> 飞书配置 -> 扫码接入
2. 创建 systemd 服务：

```bash
sudo tee /etc/systemd/system/quant-feishu-bot@.service > /dev/null <<'EOF'
[Unit]
Description=Quant Feishu Bot %i
After=network.target redis.service

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/quant/server
EnvironmentFile=/opt/quant/server/.env
Environment=QT_QPA_PLATFORM=offscreen
ExecStart=/opt/quant/server/venv/bin/python -m src.feishu_bot.ws_client %i
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now quant-feishu-bot@<飞书配置ID>
```

---

## 7. 数据初始化

### 7.1 全量同步（约 30-60 分钟）

登录 Web -> 数据管理 -> 选择同步类型 -> "全量同步全部"。

或命令行：
```bash
cd /opt/quant/server
source venv/bin/activate
python -c "from src.data_sync.engine import sync; sync('astock_daily')"
```

### 7.2 配置 AI 模型（可选但推荐）

登录 Web -> 系统设置 -> AI 模型 -> 添加：
- Provider: `deepseek` / `ark` / `glm`
- Model: `deepseek-chat` / `ark-code-latest` / `glm-4-flash`
- API Key: 你的 key
- Base URL: 模型 API 地址
- Priority: 数字越小越优先（多模型 fallback）

---

## 8. 常见问题

| 症状 | 原因 | 解决 |
|---|---|---|
| `psql: password authentication failed` | DB 密码不对 | 检查 `.env` 的 `QUANT_DB_URL` |
| `relation "xxx" does not exist` | schema 未初始化 | `alembic upgrade head` |
| 前端白屏 | 路由 history 未 fallback | Web 服务器配置 `try_files $uri /index.html` |
| Celery 不执行 | Redis 未连 | `redis-cli ping` 确认；检查 `VALKEY_URL` |
| `vnpy_xtp import error` | vnpy_xtp 未编译 | 参见 §3.6（仅实盘需要） |
| 飞书无回复 | AI 模型配额用完 | Web 加 fallback 模型（低 priority） |
| 磁盘满 | 历史数据 3GB | `.env` 改 `SYNC_START_DATE=20200101` |
| LLM 无响应 | API key 无效/配额满 | Web -> AI 模型 -> 测试；加 fallback |

---

## 9. 日常运维

### 更新代码

```bash
cd /opt/quant
git pull
cd server && source venv/bin/activate && pip install -r requirements.txt
alembic upgrade head    # 如有新迁移
sudo systemctl restart quant-web-api quant-celery-worker quant-celery-beat
```

### 更新前端

```bash
cd /opt/quant/web
npm run build
# rsync dist/ 到 Web 服务器静态目录
```

### 数据库备份

```bash
# 参见 server/scripts/backup-db.sh（pg_dump + gzip + 保留 7 天）
# 加入 crontab:
crontab -e
# 0 2 * * * /opt/quant/server/scripts/backup-db.sh
```

### 日志查看

```bash
sudo journalctl -u quant-web-api -n 50 --no-pager
sudo journalctl -u quant-celery-worker -n 50 --no-pager
sudo journalctl -u quant-feishu-bot@<id> -n 50 --no-pager
```

---

## 10. 安全建议

- `.env` 文件 `chmod 600`，不提交到 git
- 修改默认 admin 密码
- `ENABLE_LIVE_TRADING=false` 生产环境默认关闭
- 定期备份数据库
- Web 服务器配置 HTTPS（certbot/Let's Encrypt）
- Redis 仅监听 127.0.0.1
- API 密钥全部加密存储（系统自动 AES），不暴露明文
- 密钥管理：一个 `SECRET_KEY` 环境变量，HKDF 派生 `JWT_SECRET`（JWT 签名）和 `ENCRYPTION_KEY`（Fernet 加密凭证）。**不要丢失** `SECRET_KEY`——丢失后所有已加密凭证（XTP 密钥、Tushare token、LLM API key 等）无法解密
- 生成 SECRET_KEY：`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
