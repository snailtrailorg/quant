# DEPLOY · 服务器部署运维手册（现行，Ansible 工件化管道）

> 面向**部署运维者**。继任 `scripts/DEPLOY.md`（bash 链已删除，git 史可考 40fb5fa）。
> **机制全貌看 `docs/architecture/A06-部署管道架构.md`**（为什么这样设计 / 权限矩阵 / 八阶段语义 / 回滚缺口 / 改链炸半径清单）；
> 设计稿原始出处 `docs/obsolete/任务归档/批3-工件化交付.md`（v3.3，已归档、内容被 A06 覆盖）。本文只给操作。

## 速查节（常规操作一页；以下命令均为实际验证过的形态）

> 全部在**控制机 `~/Projects/quant/deploy/` 目录下**跑（否则 `ansible.cfg` 不生效，缺省 inventory=沙箱是安全缺省）。**改机制（wrapper/sudoers/nginx/playbook）不走本节**——先通读 A06 + §11 输入→消费者地图。

```bash
# ① 彩排（强制先行——staging 不绿不上产）
.venv/bin/ansible-playbook -i inventory/quant-staging.yml playbooks/release.yml

# ② 上产（staging 绿后）
.venv/bin/ansible-playbook -i inventory/quant-prod.yml playbooks/release.yml

# ③ 回滚（手动；release 失败时 rescue 已自动执行同款）
.venv/bin/ansible-playbook -i inventory/quant-prod.yml playbooks/rollback.yml -e confirm=true

# ④ 发布后核对三证（翻链/心跳/版本收敛）
.venv/bin/ansible quant-prod -i inventory/quant-prod.yml -m shell -o -a \
  'readlink -f /data/websites/snailtrail.cc/quant/server; sudo -n -u quant /usr/local/sbin/quant-hbcheck; sudo -n /usr/local/sbin/quant-pinned'
# 预期：server→releases/<新id>；hub 心跳 8 字段；pinned={新id}

# ⑤ 查服务日志（quant-journal wrapper 只读通道；deploy 身份）
.venv/bin/ansible quant-prod -i inventory/quant-prod.yml -m shell -o -a \
  'sudo -n /usr/local/sbin/quant-journal -u quant-web-api@quant.service --since "1 hour ago" -n 200'

# ⑥ 手动触发安全扫描（POST /api/security/scan 端点尚不存在——此为唯一手动通道，
#    2026-10-12 验证：task 1bdbf58d 全绿。在服务器上跑（michael shell 或等效 sudo 通道））
sudo -u quant bash -c 'set -a && source /data/websites/snailtrail.cc/quant/shared/.env && set +a && cd $(readlink -f /data/websites/snailtrail.cc/quant/server) && /data/websites/snailtrail.cc/quant/shared/venv/bin/python -c "from src.scheduler.app import app; print(\"task:\", app.send_task(\"src.scheduler.tasks.security_scan\").id)"'
# （安全扫描手动触发——michael root 会话跑；.env 只在子进程 source 不落 shell 历史；扫描结果见 web 观察→安全页签）
# 出 task: <uuid> 即已入队；跑完（~1-2min）看 Web 观察页安全 Tab 或 journal

# ⑦ 查 readyz（依赖可达性：PG/Valkey）
.venv/bin/ansible quant-prod -i inventory/quant-prod.yml -m shell -o -a \
  'curl -s http://127.0.0.1:8001/readyz; echo'
```

**纪律**：部署命令一律落文件 `> /tmp/rel.log 2>&1`；输出为空先验状态（④三证）再决定重跑；两环境连跑要拆开（10min 硬杀会绕过 rescue）；交易日 08:55-11:35 / 12:40-15:05 闸拦启动（紧急 `-e force=true` 自担风险）。

## 三命令（控制机 ~/Projects/quant/deploy 下）

```bash
# ① 彩排（强制先行——staging 不绿不上产）
.venv/bin/ansible-playbook -i inventory/quant-staging.yml playbooks/release.yml

# ② 发布（八阶段：交易窗→指纹→导入冒烟→DDL 门→波次重启→postverify→收敛断言→GC）
.venv/bin/ansible-playbook -i inventory/quant-prod.yml playbooks/release.yml

# ③ 回滚（任一阶段失败 rescue 自动执行；手动回滚**必须带 confirm=true**——防手滑）
.venv/bin/ansible-playbook -i inventory/quant-prod.yml playbooks/rollback.yml -e confirm=true
```

**退出码语义**：中止/回滚=非零退出+告警——CI 门不吃假绿。release 成功后核对三证：

```bash
.venv/bin/ansible quant-prod -i inventory/quant-prod.yml -m shell -o -a \
  'readlink -f /data/websites/snailtrail.cc/quant/server; sudo -n -u quant /usr/local/sbin/quant-hbcheck; sudo -n /usr/local/sbin/quant-pinned'
# 预期：server→releases/<新 id>；hub 心跳 8 字段；pinned= {新 id}（九单元收敛）
```

## 布局与特权（三权分立）

```
/data/websites/snailtrail.cc/quant/
├── releases/<id>/      # 不可变工件（deploy 写）；server 符号链接→current release（quant-flip-server 原子切）
├── shared/             # .env/venv/runtime/avatars（quant:quant，deploy 不可读 .env）
└── var/                # deploy 状态（指纹/部署日志）
```

- **root/michael 退出管道**：deploy 用户经 sudoers 白名单调 **12 只 wrapper**（root 属主 7：quant-flip-server / **quant-flip-web** / quant-install-units / quant-pinned / **quant-journal** / **quant-emd-build** / quant-svc；quant 属主 5：quant-alembic-wrapper / quant-importsmoke-wrapper / quant-pip-wrapper / **quant-dbro** / **quant-hbcheck**）——无裸 systemctl/裸 alembic/裸 psql。**wrapper 内容变更不进 release 管道，须 michael 重装位才生效**（清单见 `deploy/templates/sudoers-quant-deploy.j2`）
- **新服务器装机**：`deploy/scripts/bootstrap_server.sh`（一次性加法：deploy 用户/sudoers/wrappers/单元）；本地彩排环境 `bootstrap_staging.sh`
- **迁移**：随 release 自动走 quant-alembic-wrapper（timeout+ON_ERROR_STOP+破坏性 DDL 门）

## 已知边界（2026-08-27 G4 实证）

- systemd 239：StartLimit 窗口内 reset-failed 后 start 仍被拒——release 波次对"窗口内 crash-loop 单元"的重启会失败（自动回滚兜底；避免在单元 crash-loop 未冷却时发布）
- 单元文件变更走 quant-install-units 通道 + 受影响波（不手工 cp + daemon-reload）
- **IM 回滚不走 rollback.yml**（批26-13，批B #0b 在案收口）：rollback 波次按"普通单元"处理 IM，但 IM 的单元形态跨版本不通用——回滚时**先判目标版本所在区间**：

### IM 手工回滚 runbook（极罕见场景）

```bash
# 情形 A：回滚目标 ≥ 批11C（3d17fc6，pool 化之后）
#   IM 单元名不变（quant-im-pool@quant.service），常规 rollback.yml 即恢复——无需本节操作。

# 情形 B：回滚目标 < 批11C（pool 化之前，IM=单 bot 模板单元）
sudo -n /usr/local/sbin/quant-svc stop quant-im-pool@quant.service
# 起旧版单 bot 模板实例：%i = bot id（int，从 im_bot_config 表查：SELECT id FROM im_bot_config WHERE enabled）
sudo -n /usr/local/sbin/quant-svc start quant-feishu-bot@<bot_id>.service
```

- **兼容性注意**（盲审 B-P1-3 核正）：`im_bot_config.credentials_encrypted` 自批11 建表（迁移 0051）起就是凭证存储且列名未变——批11C（迁移 0073）只 UPDATE `owner_user_id` 归属制，**情形 B 区间（0051~0072 间）回滚后旧版读同一列，凭证无需重录**。真正不可回滚的边界=批11 之前（IM 凭证在 `feishu_config` 表，0052 已 DROP 该表）——回滚目标早于批11 时 `im_bot_config` 表不存在，IM 整体不可用，需另行评估而非套本节。
- **迁移方向注意**：0073（IM 用户化）之后前滚回现代版本不受影响；本节只在"整体回退到 pool 化之前"时使用。

## 规约节（wrapper 生命周期 + 检查准入——批 121 立）

### wrapper 生命周期：变更 ⇒ 重跑 bootstrap，手工 install = 违规

- **唯一装位通道**：wrapper 内容或 sudoers 模板变更后，**重跑 `deploy/scripts/bootstrap_server.sh`**（生产）/ `bootstrap_staging.sh`（彩排）——幂等纯加法，可增量重跑。手工 `sudo install` 单件直装 `/usr/local/sbin` = **违规操作**。
- **为什么立此规**：wrapper 是 sudoers 白名单命令（改内容=改提权面），且**不进 release 管道**——仓库改了不等于机上生效。2026-10-12 实测 staging 与仓库基线**分叉 5 只**（缺 3：journal/dnf/ssh；旧版 2：pip-wrapper/hbcheck）——全是手工装位无机制所致：staging 彩排「价值削弱」（彩排绿了 prod 仍可能撞缺位）。
- **双机都要跑**：staging 与 prod 同路径同权限同 sudoers，装位动作两边各跑一次（michael 通道）；装完 md5 核对三方一致（staging=prod=仓库）。
- **规约前置**：三清单对齐（`bootstrap_server.sh` 装位清单 / 仓库 `wrappers/` 14 只 / sudoers 模板三别名成员）——清单互不一致时先对齐再立规，防止规约固化旧集。

### 新增检查的准入判据：必须能回答「守什么不变量」

- 发布链上加任何门/断言/守卫前，必须能用一句话回答：**它守的是哪条不变量**——锚点是 A06 §1.2 四条地基不变量（钉版本=进程 cwd / 版本树不可变 / 改 wrapper=提权 / 退出码语义）或 §6 已登记闸门。
- **答不出=不加**：守不住不变量的检查是防御性噪音（release.yml 现有约 30 个门/断言任务中过半属此类——批 121 审计裁决表另立批清理）。正确姿势是**修流程**（让不变量自己说话），不是叠检查。
- **删检查同样要过这道门**：删除任何检查须先证明它守的不是不变量（批 121 裁决表纪律）。
- 新增 server 侧输入（文件/目录）进 `deploy_slices` + `deploy_sync_whitelist` **双写**；改输入先查 A06 §11 地图知下游。
