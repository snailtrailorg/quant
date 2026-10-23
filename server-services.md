# server-services.md · prod 服务清单（systemd/Polkit/Nginx/日志）

> **仓内真源**（2026-10-23 落仓，原只在 agent 记忆中）。状态文档：单元变更随批重写。
> 日常启停/重启**一律走发布管道或 quant-svc wrapper**，禁 systemctl 裸操作（sudoers 白名单只认 wrapper）。

## 核心服务（全部 User=quant，run-current 形态）
| 服务 | 端口/说明 | 运维 |
|---|---|---|
| `quant-web-api@quant` | uvicorn :8001 | 发布管道 或 `quant-svc <verb> <unit>` wrapper |
| `quant-celery-worker@quant` | 通用 worker | 同上 |
| `quant-celery-beat@quant` | 定时调度（schedule 落 shared/runtime） | 同上 |
| `quant-celery-risk@quant` | 风控专属（sa4-reconciler beat 300s） | 同上 |
| `quant-md-hub@quant` | 共享行情 Hub | 同上；心跳 `quant:hb:md-hub`（HASH 8 字段） |

## 单元形态
- `ExecStart=/data/.../quant/bin/run-current -m src.…`；`WorkingDirectory=.../shared`（禁穿 server 链接——版本收敛断言前提）；`EnvironmentFile=shared/.env`
- 单元变更只经发布管道 quant-install-units 通道（A06 波次：加单元须进波次清单）

## 模板单元（按需启停）
| 服务 | 当前实例 | 说明 |
|---|---|---|
| `quant-feishu-bot@N` | 按 im_bot_config enabled（quant-dbro feishu 查） | 波次清单驱动 |
| `quant-live-task@N` | 按 live_task running（quant-dbro live 查） | 实盘任务子进程 |
| `quant-task-failed@` | — | OnFailure 告警钩子（oneshot） |

## 通用配置
- hub/live-task：WatchdogSec=90、MemoryMax=1G、CPUQuota=50%、StartLimitBurst=5/300s
- Polkit `49-quant.rules`：quant 可管 strategy/live-task/feishu（md-hub/web-api 走管道 wrapper）
- Nginx（`/etc/nginx/conf.d/quant.conf`）：反代 :8001、SSL LE、/metrics 与 /api/_probe 内网 allowlist、前端 index.html 不缓存

## 日志（只读通道，常态零接触——管道 postverify/告警链负责发现）
- `sudo -n /usr/local/sbin/quant-journal -u <unit> [--since "-N min"] [-n N]`
- hub 生命周期滤 `窗|挂起|重连|登录|已确认`；worker bar 逐分 `grep BAR`
- 排障钥匙：`journalctl -u <unit> --since '-N min' --no-pager`
