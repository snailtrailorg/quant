# server-info.md · prod 服务器信息与文件系统地图

> **仓内真源**（2026-10-23 落仓，原只在 agent 记忆中——CLAUDE.md 悬空指针修复）。
> 密钥/密码一律写指针不写明文；本文件是**状态文档**：结构变化随批重写使其自洽。
> 配套：服务清单见 `server-services.md`；部署机制真相源 `docs/architecture/A06-部署管道架构.md`。

## 机器与访问模型
- **prod**：`quant.snailtrail.cc`（120.24.235.98），Alibaba Cloud Linux 3（al8/RHEL8 系，内核 5.10.134），SELinux 关闭；内存 1.8G+swap；Python 3.11（shared/venv）
- **staging**：`staging.local`（彩排机，同路径/同权限/同 sudoers/真 ssh；**PG 与本地 dev 共用同一实例——dev 侧 alembic 升降级会打穿彩排前置**，批 142 实证；expectations 重跑用 chain_scratch schema 勿升真库。本地 pg_hba **按库名 trust**：quant 库免密、postgres 库与新库 md5——`-d postgres` 在管道里=密码提示读 /dev/tty 永久挂起
- 访问：root **不直连 ssh**；michael ssh+sudo（一次性/救火）；**常态唯一通道=`deploy/.venv` 的 ansible**（deploy 密钥 ~/.ssh/quant_deploy_ed25519 + sudoers 白名单 wrapper，禁裸 ssh/scp——连上也是死路）

## 部署根（/data/websites/snailtrail.cc/quant/）
| 路径 | 是什么 | 真源关系 |
|---|---|---|
| `server` | 符号链接 → `releases/<id>`（当前版） | release 阶段 7 原子切换；版本收敛断言 `quant-pinned` |
| `releases/<id>/` | 工件化发布（<id>=yyyyMMddHHmm-<sha7>） | GC 保留 N=5（deploy 管道收尾阶段）；回滚=切链接 |
| `releases/<id>/deps`,`vendor` | 轮子切片（真 vnpy wheel 等） | pip wrapper --require-hashes 硬断言；**每版一份=磁盘大头嫌疑**（批 143 取证） |
| `shared/` | .env + venv + runtime + avatars（quant 0700，secrets 隔离） | **不随版本切换**；venv 由管道 quant-pip-wrapper 维护 |
| `shared/.env` | QUANT_DB_URL / SECRET_KEY 等（只放密钥，其余配置入库） | 密钥加密入库立法见 A02 |
| `web` | 符号链接 → 前端 dist 版本 | quant-flip-web 原子切换 |
| `var/` | deploy 状态（快照/指纹/标记） | 管道读写，勿手碰 |
| `bin/run-current` | 服务入口（穿 shared 的稳定启动器） | systemd ExecStart 唯一指向 |

## 数据与备份（批 143 治理后终态 2026-10-23）
- **PG18**：QUANT_DB_URL 在 shared/.env；alembic 经 `quant-alembic-wrapper`（发布阶段 5）；**PGDATA=`/data/databases/pgsql`（非默认路径）**。库 ~11G=活数据紧凑态（bar_1d heap 2.6G+idx 2.1G / daily_basic 1.1G+0.9G——批 143 REINDEX+VACUUM FULL 后）；**grype-db 3.1G=单活动漏洞库（grype v6）正常体量，勿清**
- **备份**：celery 任务 `backup_db`（beat 02:00 queue=data，批 159 收编——四道防线：双 rc/tmp 原子转正/流式/空间预检；**cron 面已清零，11-01 删 crontab+装位收官**），产物 `/home/quant/backups/`，**保留最新 3 份**（2026-10-31 裁定）；dump ~930M/份；**恢复演练已过（备份可兜底）**，工具 `scripts/backup-restore-drill.sh`（本地跑，前置临时授权见脚本头）
- **磁盘水位**：根盘 40G 用量 78%（journal 已 cap 200M）；bar 表无分区/清理策略=矩阵 F-17 开放项
- **KV 后端（批 162 迁移终态 2026-11-02）**：valkey 8.0.11 @ 6379（redis 6.2 已停+disable，RDB 原地留作回滚保险一个发布周期）；**自启=enabled（2026-11-08 补——批 162 漏 enable 致 11-07 服务器重启后 KV 全瘫、发布被 readyz 闸拦，教训见踩坑记录）**；分库：db0=空（DEFAULT 备而未用）、**db4=业务+feishu 长连接（~285 键）**、**db5=Celery broker（~27 键）**、**db6=Celery result（TTL 堆积常态）**；关键键：`quant:hb:md-hub`(HASH 8 字段)、`quant:hb:task:{tid}`、`hub:lease`/`hub:gen`(fencing)。⚠️ 排障裸连（redis-cli/valkey-cli 默认 db0=空库）——业务查询必须 -n 4（11-02 裸连 None 假象教训）。迁移教训：BGSAVE 异步返回≠落盘完成，拷 RDB 前必须核 dump.rdb 时间戳（11-02 拷到昨日 RDB 差 84 条 stream 尾的对拍拦截）
- **密钥**：SECRET_KEY 单根密钥，HKDF 派生 JWT_SECRET + ENCRYPTION_KEY(Fernet)
- **OS 级调优**：`vm.overcommit_memory=1`（2026-11-02 随批 162 开；运行时+`/etc/sysctl.d/99-valkey-overcommit.conf` 持久化）——KV 后端 BGSAVE fork 依赖 overcommit，关闭时低内存下后台保存可能失败；valkey 启动日志会 WARN

## 自检命令（AI 对应检查用；一律走 deploy/.venv ansible）
```bash
cd deploy
.venv/bin/ansible quant-prod -i inventory/quant-prod.yml -m shell -o -a \
  'readlink -f /data/websites/snailtrail.cc/quant/server; readlink -f /data/websites/snailtrail.cc/quant/web'   # 当前版本指针
.venv/bin/ansible quant-prod -i inventory/quant-prod.yml -m shell -o -a \
  'du -x -d1 /data/websites/snailtrail.cc/quant/ | sort -rh | head'   # 磁盘构成（批 143 取证同款）
.venv/bin/ansible quant-prod -i inventory/quant-prod.yml -m shell -o -a \
  'sudo -n -u quant /usr/local/sbin/quant-hbcheck; sudo -n /usr/local/sbin/quant-pinned'   # 心跳+版本收敛三证
```
