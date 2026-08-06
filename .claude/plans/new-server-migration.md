# 新服务器迁移 + 部署方案优化

## 用户决策
- 数据迁移：**都重新初始化**（safebox 用户数据也重建，最干净）
- 全量起点：**2010-01-01 全历史**（约 3GB bar_1D）
- safebox-deploy.sh：**改**（clear-redis FLUSHDB -n 0, restart-web reload）

## 一、当前部署的脆弱点（这次踩的 6 个坑）

1. **schema 分散**：`CREATE TABLE IF NOT EXISTS` 散在各 handler，新库初始化鸡生蛋（audit_log 缺列 / sync_log 缺表 / bar_1D 缺表+owner）
2. **.env db 号冲突**：quant 误用 safebox 的 db0
3. **磁盘无规划**：全量 3GB+ 没预估，差点撑爆
4. **safebox-deploy.sh `clear-redis` 用 FLUSHALL**：会清掉 quant 的 db
5. **状态展示不一致**：全量走 Celery，DataManage 页看 last_status 看不到进度
6. **脚本脆弱**：依赖检查 / 文件部署 / 转义多个 bug

## 二、优化方案（7 点）

### 1. 集中 init-schema.sql（解决鸡生蛋）
- 新建 `server/scripts/init-schema.sql`：建**所有**业务表（owner=quant），幂等
- 表清单：users / audit_log / sync_config / sync_log / bar_1D / daily_basic / asset_static_info / cb_basic_info / etf_basic_info / trade_cal
- 所有表 `ALTER TABLE ... OWNER TO quant`（避免之前 owner=postgres 的权限问题）
- 部署时跑一次：`psql -d quant -f scripts/init-schema.sql`
- 保留各 handler 的 `CREATE TABLE IF NOT EXISTS`（运行时兜底，但 init-schema 是主）

### 2. quant-deploy.sh 加固 + init-schema 动作
- 加 `init-schema` 动作：`psql -d quant -f scripts/init-schema.sql`
- 加固依赖检查（已改 `-e` + 文件部署，保留）
- 首次部署流程：`deploy server/ -> init-schema -> init-seed -> restart`

### 3. .env.example db 隔离 + 生成脚本
- `server/.env.example` 明确 db 分配：
  ```
  # safebox=db0, quant: VALKEY=db4, CELERY_BROKER=db5, CELERY_RESULT=db6
  VALKEY_URL=redis://127.0.0.1:6379/4
  CELERY_BROKER_URL=redis://127.0.0.1:6379/5
  CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/6
  ```
- 加 `SYNC_START_DATE=20100101`（全量起点，可配）
- 加 `scripts/init-env.sh`：从 .env.example 生成 .env，提示填密码/token（交互式，避免手动 sed 出错）

### 4. SYNC_START_DATE 可配（磁盘规划）
- `engine.py` 的 `_TUSHARE_MIN_DATE` 改读环境变量：`os.environ.get("SYNC_START_DATE", "20100101")`
- .env 配 `SYNC_START_DATE=20100101`（全历史，用户选）
- DEPLOY.md 说明数据量：bar_1D 全量 2010-今 约 3GB，daily_basic 约 3GB，**新服务器磁盘建议 40GB+**

### 5. safebox-deploy.sh 改两处（避免破坏 quant）
- `clear_redis`：`FLUSHALL` -> `FLUSHDB -n 0`（只清 safebox db0）
- `restart_web`：`systemctl restart httpd` -> `systemctl reload httpd`（不断连接）
- 改 safebox 仓库 `scripts/safebox-deploy.sh`，重新 cp 到 `~/.local/bin/`

### 6. DataManage 展示 Celery 进度
- `tasks.py` 的 `sync_all_symbols` 开始时 `_mark_running(True)` + 完成时 `_mark_running(False)` + 更新 `last_sync_count`
- 这样 DataManage 页 last_status / last_sync_count 反映全量进度
- SymbolManage 页进度条已有，保留

### 7. 状态机自愈（last_status 僵尸）
- `sync_all_symbols` 任务用 SyncLock（已有），last_status 退化展示
- 或前端展示查 Valkey 锁状态（`sync:lock:{sid}` 存在=运行中），不依赖 last_status
- 本棒先做 _mark_running（简单），前端锁状态展示下棒

## 三、落地文件清单

| 文件 | 改动 |
|---|---|
| `server/scripts/init-schema.sql` | 新建，集中建所有表 + OWNER quant |
| `server/scripts/init-seed.sql` | 保留（sync_config 种子），init-schema 后跑 |
| `scripts/quant-deploy.sh` | 加 `init-schema` 动作 |
| `server/.env.example` | db4/5/6 + SYNC_START_DATE=20100101 |
| `scripts/init-env.sh` | 新建，从 .env.example 生成 .env（交互填密码） |
| `server/src/data_sync/engine.py` | `_TUSHARE_MIN_DATE` 读环境变量 |
| `server/src/scheduler/tasks.py` | `sync_all_symbols` 加 _mark_running + 更新 last_sync_count |
| `safebox/scripts/safebox-deploy.sh` | clear_redis FLUSHDB -n 0, restart_web reload（改 safebox 仓库） |
| `DEPLOY.md` | 新服务器部署指南 + 迁移步骤 + 磁盘规划 |

## 四、新服务器初始化（一次性）

```bash
# 1. 装 PG18/Redis6/Apache/python3.11/certbot
sudo dnf install -y postgresql15-server redis6 httpd python3.11 certbot python3-certbot-apache

# 2. 初始化 PG（数据目录 /data/databases/pgsql）+ pg_hba md5
# 3. 建 michael 用户 + sudoers（bernard 只能跑两个 deploy 脚本）
# 4. 建 safebox/quant 系统用户 + 目录
# 5. 装 safebox-deploy.sh + quant-deploy.sh 到 /home/michael/.local/bin/
```

## 五、安全模型（保持，已满足）

- michael 用户：ssh 到服务器 + sudo 操作
- bernard：只能 `sudo -u michael /home/michael/.local/bin/{safebox,quant}-deploy.sh`（sudoers NOPASSWD 限定）
- 脚本内部 ssh + sudo 操作远程
- sudoers 文件 `/etc/sudoers.d/snailtrailorg-deploy`：
  ```
  bernard ALL=(michael) NOPASSWD: /home/michael/.local/bin/safebox-deploy.sh
  bernard ALL=(michael) NOPASSWD: /home/michael/.local/bin/quant-deploy.sh
  ```

## 六、迁移步骤（新服务器）

1. **新服务器初始化**（PG/Redis/Apache/用户/sudoers/目录）
2. **装两个 deploy 脚本**到 `~/.local/bin/`
3. **safebox 部署**（用 safebox DEPLOY.md 全新部署，数据重新初始化）
4. **quant 部署**：
   - `deploy server/` 代码
   - 建 venv + pip install
   - `init-env.sh` 生成 .env（填密码/token/db4-6/SYNC_START_DATE）
   - `init-schema` 建所有表
   - `init-seed` 插 sync_config 种子
   - systemd 3 服务
   - Apache vhost + 证书
   - 首次全量同步（从 2010，约 3GB，37 分钟）
5. **DNS 切换**：quant.snailtrail.org + safebox.snailtrail.org 指向新服务器

## 七、磁盘规划

| 项 | 大小 |
|---|---|
| bar_1D 全量（2010-今，5534只×3950天） | ~3GB |
| daily_basic 全量 | ~3GB |
| safebox 库 | <1GB |
| PG 日志 + 备份 | ~5GB |
| **建议新服务器磁盘** | **40GB+** |

## 本棒范围与不做

**做**：7 点优化 + 落地文件 + DEPLOY.md 迁移指南 + safebox-deploy.sh 改进建议。

**不做**：
- 实际新服务器操作（用户手动执行 DEPLOY.md）
- safebox 仓库代码改动（给改进建议，用户在 safebox 项目改）
- 前端锁状态展示（下棒）
