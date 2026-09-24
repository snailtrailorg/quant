# DEPLOY · 服务器部署运维手册（现行，Ansible 工件化管道）

> 面向**部署运维者**。继任 `scripts/DEPLOY.md`（bash 链已删除，git 史可考 40fb5fa）。
> 完整制度与设计：`docs/obsolete/任务归档/批3-工件化交付.md` + 记忆 `deploy-mechanism`；本文只给操作。

## 三命令（控制机 ~/Projects/quant/deploy 下）

```bash
# ① 彩排（强制先行——staging 不绿不上产）
.venv/bin/ansible-playbook -i inventory/quant-staging.yml playbooks/release.yml

# ② 发布（八阶段：交易窗→指纹→导入冒烟→DDL 门→波次重启→postverify→收敛断言→GC）
.venv/bin/ansible-playbook -i inventory/quant-prod.yml playbooks/release.yml

# ③ 回滚（任一阶段失败 rescue 自动执行；手动回滚同款）
.venv/bin/ansible-playbook -i inventory/quant-prod.yml playbooks/rollback.yml
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

- **root/michael 退出管道**：deploy 用户经 sudoers 白名单调 9 只 wrapper（quant-svc/quant-pinned/quant-flip-server/quant-alembic-wrapper/quant-importsmoke-wrapper/quant-pip-wrapper/quant-dbro/quant-hbcheck/quant-install-units）——无裸 systemctl/裸 alembic
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
