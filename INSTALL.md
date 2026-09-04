# 安装指南（独立实例部署手册）

> **读者**：独立部署本平台一套实例的人（自有服务器/交付场景）。假设读者不了解本项目内部历史，
> 只需要：按步骤得到一套运行中的系统。
> **姊妹文档**：装完后日常发布/回滚 → `deploy/DEPLOY.md`；日常使用 → `docs/操作指导/索引.md`。
> **诚实声明**：本手册由在产形态反推成文（2026-08-28），各阶段组件均在生产验证过，
> 但"干净机器整链首装"未演练——首个独立实例交付时请按册走一遍并把偏差回改本文件（守则：过/未验分列）。

## 0. 安装弧总览

| 阶段 | 在哪台机器 | 干什么 | 一次性？ |
|---|---|---|---|
| A 基础设施 | 服务器 | 系统包/PG+pgvector/Valkey/Nginx | 是 |
| B 管道装机 | 控制机→服务器 | Ansible bootstrap：deploy 用户+sudoers+9 wrapper+9 systemd 单元 | 是 |
| C 应用首装 | 控制机+服务器 | shared 层（.env/venv）→ 首次 release（建 schema）→ 前端 | 是 |
| D 首次配置 | 浏览器 | 改密/数据源/AI/IM/告警 | 是 |
| E 验证 | 任意 | healthz/心跳/数据全量同步 | 是 |
| 日常 | 控制机 | 发布/回滚三命令+备份 | 持续 |

预期总时长：基础设施+装机约 1 小时；数据全量同步 30-60 分钟（后台）。

---

## 1. 前置条件

### 1.1 服务器规格

| 资源 | 最低 | 推荐 |
|---|---|---|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 20 GB | 40 GB+（历史数据约 3GB） |
| OS | RHEL 系（dnf）或 Debian 系（apt），任意现代发行版 | Alibaba Cloud Linux 3 / Rocky 9 |

### 1.2 软件依赖

| 软件 | 版本 | 说明 |
|---|---|---|
| Python | **3.10 或 3.11（不支持 3.14）** | vnpy 4.4.0 的 PySide6 pin 所致；纯 Python 组件 3.10-3.13 均可，实盘卡点在 vnpy_xtp |
| PostgreSQL | 15+（推荐 18） | 需 **pgvector** 扩展 |
| Valkey（或 Redis） | 6.0+ | Valkey 是 Redis 协议兼容开源替代 |
| Node.js | 18+ | 仅前端构建用，可装在控制机，服务器不需要 |
| Nginx | 任意稳定版 | 反代 API + 前端静态 |
| gcc / python3-devel | — | vnpy_xtp 编译（仅实盘需要） |
| pango / cairo / gdk-pixbuf2 / 中文字体 | — | weasyprint（回测 PDF 导出）系统库；中文字体需 wqy-microhei-fonts（或 Noto CJK），否则 PDF 中文豆腐块 |

### 1.3 账号与密钥清单（开工前备齐）

- [ ] **Tushare token**（数据源，[tushare.pro](https://tushare.pro) 注册获取；200 积分即可跑日线）
- [ ] **LLM API key** 至少一个（DeepSeek 主 / GLM 备——运行期 AI 只用国内模型）
- [ ] **根密钥 SECRET_KEY**：`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` 现场生成——HKDF 派生 JWT 签名钥+凭证加密钥，**丢了所有已存凭证不可解密，务必备份**
- [ ] 可选：中泰 XTP 账户+SDK（A 股/可转债/ETF 实盘）；飞书自建应用（IM 机器人）
- [ ] 控制机 → 服务器 root 的 ssh 一次（仅 bootstrap 阶段用；此后 root 退出部署通道）

### 1.4 控制机要求

git / python3 / 能 ssh 到服务器。其余由仓库 `deploy/requirements.txt` 自装。

---

## 2. 阶段 A · 基础设施（服务器，root）

### 2.1 系统包

**RHEL 系：**
```bash
dnf install -y postgresql-server postgresql-devel valkey nginx git rsync pango cairo gdk-pixbuf2 libffi wqy-microhei-fonts
# Python 3.11（若系统默认非 3.10/3.11）：dnf install -y python3.11 python3.11-devel gcc gcc-c++ make
```
**Debian 系：**
```bash
apt install -y postgresql postgresql-server-dev-all valkey nginx git rsync libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 fonts-wqy-microhei
apt install -y python3.11 python3.11-dev gcc g++ make   # 按发行版调整
```

### 2.2 PostgreSQL：初始化 + pgvector + 角色/库

```bash
postgresql-setup --initdb          # RHEL 系；Debian 系安装即初始化
systemctl enable --now postgresql valkey nginx

# pgvector：按发行版装（dnf install postgresql16-vector / apt install postgresql-16-pgvector，
# 或源码：https://github.com/pgvector/pgvector）

sudo -u postgres psql <<'SQL'
CREATE ROLE quant LOGIN PASSWORD '自定强密码';
CREATE DATABASE quant OWNER quant;
\c quant
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL
```

> Valkey/PG 仅监听 127.0.0.1 即可（平台组件全部同机）；分库约定：Valkey db4=业务 / db5=celery broker / db6=celery result（多应用共机时避开他方 db）。

### 2.3 Nginx

仓库提供样例 `server/scripts/nginx/quant.conf`——复制到 `/etc/nginx/conf.d/` 后改两处：
`server_name` 与证书路径（HTTPS 用 certbot 或自有证书；前端静态 root 见 §4.3 说明，路径变量化为你的部署根）。

---

## 3. 阶段 B · 部署管道装机（控制机，一次性）

```bash
git clone <你的仓库地址> && cd quant/deploy
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

**指向你的服务器**：`deploy/inventory/quant-prod.yml` 改 `ansible_host`（IP/域名）与 `ansible_user`；
`deploy/inventory/group_vars/all.yml` 按需改部署根路径（默认 `/data/websites/<你的域>/quant`）。

**bootstrap（特权层装机）**：
```bash
.venv/bin/ansible-playbook -i inventory/quant-prod.yml playbooks/bootstrap.yml -e bootstrap_enabled=true
```
自动完成：deploy 用户+quant 组、sudoers 白名单、9 只特权 wrapper（`/usr/local/sbin/quant-*`）、
`run-current`、9 个 systemd 单元模板（`server/scripts/systemd/` 为真相源）、目录权限矩阵
（`releases/` `var/` `shared/`）。此后 **root/michael 退出部署通道**。

验证：
```bash
.venv/bin/ansible quant-prod -i inventory/quant-prod.yml -m shell -a \
  'id deploy; ls /usr/local/sbin/ | grep quant- | wc -l; systemctl list-unit-files "quant-*" --no-legend | wc -l'
# 预期：deploy 用户存在 / wrapper ≥9 / 单元模板 ≥9
```

---

## 4. 阶段 C · 应用首装（一次性）

### 4.1 shared 层

**`.env`**（quant 用户属主 600，deploy 不可读）：从 `server/.env.example` 复制为
`<部署根>/shared/.env`，逐键填写：

```ini
QUANT_DB_URL=postgresql://quant:密码@127.0.0.1:5432/quant
VALKEY_URL=redis://127.0.0.1:6379/4        # db4=业务（§2.2 约定）
CELERY_BROKER_URL=redis://127.0.0.1:6379/5
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/6
TUSHARE_TOKEN=...
DEEPSEEK_API_KEY=...                        # LLM 至少一个；GLM 备可选
DEEPSEEK_BASE_URL=https://api.deepseek.com
SECRET_KEY=...                              # §1.3 生成的根密钥，务必备份
ENABLE_LIVE_TRADING=false                   # 三级开关的总闸；装完默认关
# SYNC_START_DATE=20200101                  # 磁盘紧张时收窄全量起点
```

**venv**（`<部署根>/shared/venv/`，quant 属主）：
```bash
python3.11 -m venv <部署根>/shared/venv
<部署根>/shared/venv/bin/pip install -r server/requirements.txt
```

**可选：vnpy_xtp 编译**（仅 A 股/可转债/ETF 实盘）：
```bash
# 中泰 XTP SDK（.so）下载放 server/vendor/xtp/lib/（xtp.zts.com.cn/service/download）
PATH=<部署根>/shared/venv/bin:$PATH CPATH=server/vendor/xtp/include \
LIBRARY_PATH=server/vendor/xtp/lib \
<部署根>/shared/venv/bin/pip install --no-build-isolation vnpy_xtp
```

### 4.2 首次发布（建 schema + 起服务）

```bash
cd deploy && .venv/bin/ansible-playbook -i inventory/quant-prod.yml playbooks/release.yml
```
管道自动：代码切片同步 releases/<id> → 导入冒烟 → pip 指纹比对 → **alembic upgrade head（建全表）**
→ 原子切换 server 链接 → 波次起服务 → postverify（healthz/readyz/心跳/版本收敛）。
失败自动回滚。发布后三证核对见 `deploy/DEPLOY.md`。

**种子数据（管道外，一次性）**：
```bash
# 服务器上（quant 身份可连库）：
psql -U quant -d quant -f <部署根>/server/scripts/init-seed.sql
```

### 4.3 前端（管道不含前端——每次发版都要做）

```bash
# 控制机构建：
cd web && npm install && npm run build          # 产物 dist/
# 上传到服务器前端目录（Nginx 静态 root，独立于 releases 版本树）：
rsync -a --delete dist/ <服务器>:<部署根>/web/
```
> Ansible 管道只管 `server/` 五切片（src/migrations/scripts/alembic.ini/requirements.txt）；
> 前端是独立静态目录，发版节奏自行掌握（`--delete` 安全：目录内只有构建产物）。

---

## 5. 阶段 D · 首次配置（浏览器）

1. 打开 `https://<你的域>` → 默认管理员 **admin / admin123 → 立即改密**
2. 管理设置 → 数据源管理：Tushare token 入库（积分档下拉选 200 档即可起步；升级积分一键切档）
3. 管理设置 → AI 模型：添加 DeepSeek（主）/GLM（备），priority 小者优先自动容灾
4. 可选：IM 接入向导（飞书扫码）；告警通道（邮件等）
5. 数据管理 → 全量同步（约 30-60 分钟，后台跑；进度看数据完整性看板）

## 6. 阶段 E · 验证

```bash
curl -s https://<你的域>/healthz        # {"status":"ok",...}
curl -s https://<你的域>/readyz         # 就绪探针
sudo -u quant /usr/local/sbin/quant-hbcheck   # 行情 hub 心跳（8 字段）——需已起 hub 单元
```
浏览器走一遍：登录/看板有数/因子页/回测一次冒烟。到此安装完成。

---

## 7. 日常运维

- **升级/回滚**：`deploy/DEPLOY.md` 三命令（彩排→发布→回滚）——不要手工改 releases 目录
- **备份**：`server/scripts/backup-db.sh`（pg_dump+gzip 保 7 天）入 crontab：`0 2 * * * <路径>/backup-db.sh`
- **日志**：`journalctl -u "quant-*" -n 100 --no-pager`（九单元模板，`@` 后为实例名）
- **实盘开启**：三级开关（.env 总闸 `ENABLE_LIVE_TRADING` + Web 分项 + 策略 `backtest_verified`）——见操作指导·实盘册

### 常见问题

| 症状 | 原因 | 解决 |
|---|---|---|
| `psql: password authentication failed` | DB 密码不对 | 查 `shared/.env` 的 `QUANT_DB_URL` |
| `relation "xxx" does not exist` | schema 未建 | 首次发布未跑/失败——重跑 release（含 alembic） |
| 前端白屏 | 路由 history 未 fallback | Nginx `try_files $uri $uri/ /index.html` |
| Celery 不执行 | Valkey 未连 | `valkey-cli ping`；查 `VALKEY_URL` db 号 |
| `vnpy_xtp import error` | 未编译 | §4.1 可选段（仅实盘需要） |
| LLM 无响应 | key 无效/配额满 | Web→AI 模型→测试；加低 priority 备模型 |
| 磁盘满 | 历史数据增长 | `.env` 收窄 `SYNC_START_DATE`；旧 releases 自动 GC 保 5 版 |
| 发布失败自动回滚 | 见管道日志八阶段哪一段 | `deploy/DEPLOY.md` 已知边界（crash-loop 冷却窗等） |

## 8. 安全清单

- `shared/.env` 属主 quant 600（bootstrap 已设）；SECRET_KEY **离线备份**（丢失=已存凭证全部不可解）
- 默认 admin 密码首登必改；`ENABLE_LIVE_TRADING` 装机默认 false
- PG/Valkey 仅监听 127.0.0.1；Nginx 上 HTTPS
- API 密钥全部 AES 加密入库（自动）；deploy 用户不可读 `.env`（三权分立见 deploy/DEPLOY.md）
- 定期跑备份脚本并演练过一次恢复

---

*本手册随平台演进修订（批 6 收口/direct 退役等形态变化时更新对应段）；历史手动装配版在 git 史可考。*
