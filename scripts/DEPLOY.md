# 部署机制（权限隔离模型）

> 本文件面向**部署运维者**，说明 bernard（开发者）如何通过 michael（运维）的权限、用受限脚本 `quant-deploy.sh` 把代码部署到服务器。
> 与项目根 `INSTALL.md`（面向**最终使用者**的全新安装指南）不同——本文件描述的是**既有服务器上的持续部署通道**。

## 1. 为什么这么复杂：权限隔离

服务器跑着多个项目（quant + safebox 等），不能给开发者 root 或共享账号。设计目标：

- **开发者 bernard 能自主部署代码**，不用每次找运维
- **bernard 拿不到服务器 shell**，不能误碰其他项目
- **bernard 能做的事固定**（只能跑预定义的部署动作，不能任意命令）
- **与 safebox 共存严格隔离**（Redis/PG/Web 各自隔离，互不影响）

## 2. 角色与能力

| 角色 | 在哪 | 能做什么 | 不能做什么 |
|---|---|---|---|
| **bernard**（开发者） | 开发机 fedora | `git push`；`sudo -u michael quant-deploy.sh <动作>` 部署 | 直接 ssh 服务器；改 `quant-deploy.sh`（michael 拥有）；任意 sudo |
| **michael**（运维） | 开发机 + 服务器 | ssh 服务器；服务器 sudo；维护 `quant-deploy.sh` | （不受限，信任主体） |
| **quant**（服务账号） | 服务器 | 跑 quant 应用（web-api/celery/feishu-bot/strategy）；读写 quant 自己的代码/数据 | sudo；碰 safebox；改 systemd unit |
| **safebox**（其他项目） | 服务器 | 各自隔离运行 | 与 quant 互不干涉 |

## 3. sudoers 配置（核心一行）

开发机 fedora 的 `/etc/sudoers.d/quant-deploy`：

```
bernard ALL=(michael) NOPASSWD: /home/michael/.local/bin/quant-deploy.sh
```

含义：bernard 可以**免密**以 michael 身份执行**这一个脚本**，别的不行。

### ⚠️ 关键安全不变量（整个模型的锚点）

**bernnard 绝不能写 `/home/michael/.local/bin/quant-deploy.sh`。**

- sudoers 允许 bernard 以 michael 身份跑这脚本；michael 在服务器上有 sudo（近乎 root）。
- 若 bernard 能改这个脚本 → 他能往里塞 `sudo bash` / 任意命令 → **直接 escalate 成 root**。
- 所以"脚本定义 bernard 能干啥"，而**脚本本身必须对 bernard 只读**。这是不可破坏的红线。

**落实方式**：
- `/home/michael/.local/bin/quant-deploy.sh` 属主 **michael:michael**，权限 **`0755`**（bernnard 可读可执行，**不可写**）。
- 仓库里的源码 `scripts/quant-deploy.sh` bernard **可以**改（在他工作目录），但那只是源码；**实际跑的是 michael 拥有的那份副本**，bernnard 改了源码不会生效，除非 michael 重新 cp。
- 升级脚本能力 = michael 改源码 + `cp` 到 `~/.local/bin/`（§7.4）。

**验证红线没被破坏**（michael 定期跑）：
```bash
ls -l /home/michael/.local/bin/quant-deploy.sh
# 必须是：-rwxr-xr-x 1 michael michael ...（属主 michael，其他人无 w）
# 若属主变成 bernard 或 others 有 w → 立即 chown michael:michael + chmod 755
```

## 4. 三个脚本的职责

```
开发机 fedora:
  ~/Projects/quant/scripts/
    deploy-server.sh   ← bernard 入口（后端部署）
    deploy-web.sh      ← bernard 入口（前端部署）
    quant-deploy.sh    ← 源码（git 跟踪，michael cp 到 ~/.local/bin/）
  /home/michael/.local/bin/
    quant-deploy.sh    ← 运行副本（michael 拥有，bernnard 只读执行）

服务器 quant.snailtrail.cc:
  /data/websites/snailtrail.cc/quant/{server,web}   ← quant 用户拥有
```

- **`deploy-server.sh` / `deploy-web.sh`**：bernnard 的便捷封装，固定动作链（deploy → fix-venv → migrate → restart-*），调 `sudo -u michael quant-deploy.sh`。
- **`quant-deploy.sh`**：实际干活的，SSH 到服务器执行。动作固定枚举（见 §5）。**改它 = 改部署能力**，需 michael cp 新版到 `~/.local/bin/`。

## 5. quant-deploy.sh 动作清单（固定能力边界）

bernnard 能用的动作（脚本里 `case` 枚举，未知动作拒绝）：

**部署**：`deploy LOCAL REMOTE [--exclude ...]`
**数据库**：`migrate`（alembic）/ `init-seed` / `init-schema` / `clear-pgsql`（destructive）/ `clear-redis`（destructive，仅 db2+db3）
**venv**：`fix-venv`（迁路径后修 shebang）/ `pip-install`
**服务**：`restart-server` / `restart-celery` / `restart-feishu` / `restart-web`（reload）/ `restart-pgsql` / `restart-redis`（共享，慎用）
**注册**：`install-services`（lint+cp service+polkit+daemon-reload）/ `enable-services`

## 6. safebox 隔离约定（脚本内置，自动遵守）

quant-deploy.sh 写死的安全规则，bernnard 绕不过：

- **clear-redis** 只 `FLUSHDB -n 2 -n 3`（VALKEY/CELERY），**绝不 FLUSHALL**（会清 safebox 的 db0）
- **clear-pgsql** 只 `DROP DATABASE quant`，**不碰 safebox 库**
- **restart-web** 用 `reload`（不断连接，不影响 safebox 流量），不用 `restart`
- **Redis DB 分配**：quant 用 db2/db3，safebox 用 db0/db1

## 7. 完整流程

### 7.1 日常部署（bernnard 改了后端代码）

```bash
# 开发机，bernnard 用户
cd ~/Projects/quant
./scripts/deploy-server.sh
# 等价于：sudo -u michael quant-deploy.sh deploy ... fix-venv migrate restart-server restart-celery restart-feishu
```

### 7.2 首次部署 / 改 systemd 服务

```bash
./scripts/deploy-server.sh install-services enable-services
```

### 7.3 迁路径 / 换服务器

1. michael 改 `quant-deploy.sh` 顶部 `SERVER_ENV` 配置块（`SITE_ROOT`/`SERVER_DOMAIN` 等）一处
2. michael cp 新脚本到 `~/.local/bin/`
3. bernard `./scripts/deploy-server.sh`（含 `fix-venv` 自动修 venv shebang）

### 7.4 改部署能力（加新动作）

只有 michael 能做：
1. 改 `scripts/quant-deploy.sh`（git commit）
2. `sudo cp scripts/quant-deploy.sh /home/michael/.local/bin/`
3. bernard 即可用了

## 8. 服务器服务清单（quant 用户跑）

| 服务 | 类型 | enable | 说明 |
|---|---|---|---|
| quant-web-api@quant | 核心 | ✅ | FastAPI 后端（uvicorn :8001） |
| quant-celery-worker@quant | 核心 | ✅ | Celery 异步任务 |
| quant-celery-beat@quant | 核心 | ✅ | Celery 定时调度 |
| quant-feishu-bot@\<fid\> | 核心 | ✅ | 飞书长连接（每 bot 一实例，fid=配置 ID） |
| quant-strategy@\<id\> | 按需 | ❌ | 实盘策略子进程（Web 通过 polkit 启停） |

polkit 规则（`/etc/polkit-1/rules.d/`）：允许 quant 用户 `systemctl start/stop quant-strategy@*`，使 Web 界面能启停策略而无需 sudo 密码。

## 9. 故障排查

| 症状 | 根因 | 处理 |
|---|---|---|
| `sudo: quant-deploy.sh: command not found` | sudoers 路径错或脚本没 cp | michael `cp` 到 `/home/michael/.local/bin/` |
| deploy 后 web-api `203/EXEC` | venv shebang 还指旧路径 | 加 `fix-venv` 动作（迁路径必加） |
| `bad unit file setting` | service 文件有 `__占位符__`/`User=%i` | 用 `install-services`（内置 lint 拒绝占位符） |
| 服务 crash-loop | 启动失败 | `restart-*` 会提示 `journalctl -u <svc> -n 30` |
| clear-redis 误清 safebox | — | 不会发生（脚本写死 db2/db3，绝不 FLUSHALL） |

## 10. 修订记录

- 2026-08-11 初版（snailtrail.cc 域名 + 路径迁移后整理；权限隔离模型 + quant-deploy.sh 动作枚举 + safebox 隔离约定）
