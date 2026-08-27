# 批5：L3 扩面 + polkit 同批 + hub 单元对齐 SA4 + desired state 归一（重排后第 5 批）

> 依据：12 号 §2.10（原批 4 内容不变）；八步法（本文件=步 1 产物）。
> 前置：批 4 代码完成（staging 彩排/部署/观察日可与本批并行——纯调度层改动独立于引擎）。

## 目标
1. **L3 扩面**：sa4_reconciler 的意图调和从仅覆盖 `quant-live-task@*` 扩至 **md-hub** + `quant-strategy@*`（废架构兼容单元）——hub 停 2.5 天同类事故不再可能（2026-08-25 SEGV 后 hub failed 15 分钟无人拉起即为实证）
2. **desired state 归一**：三处真相源（live_task.status / strategy_config.enabled / md-hub 常开语义）归为 reconciler 内一张声明式期望表——一处改三处不再漏
3. **polkit 同批**：`49-quant.rules` 增 `quant-md-hub@*` 放行（reconciler 以 quant 跑 celery，需 polkit 授权 systemctl start md-hub——**不加则 L3 扩面空转**，两件必须同批）
4. **hub 单元对齐 SA4**：`Restart=always`→`on-failure`+`RestartPreventExitStatus=78`；补 `MemoryHigh=768M`——hub 配置错应 Failed 告警人工而非无限重启
5. **L3 测试清账**：现有 18 测零 L3 覆盖（grep `l3` 仅 1 命中且非断言）——补 L3 段测试

## 依赖（就绪）
批 3 管道在管（单元变更走 quant-install-units 通道自动 daemon-reload）；批 4 trading/skeleton 无关本批。

## 产出
| 文件 | 动作 | 内容 |
|---|---|---|
| `server/src/scheduler/tasks.py` | 修改 sa4_reconciler | ①期望表：`_desired_units(conn)` 返回 `[(unit, source)]`——live_task running→live-task@{tid}；strategy_config enabled 且非 live_task 关联→strategy@{id}（废架构语义不变）；md-hub→常开（desired=true，除非维护标记）②调和循环统一消费期望表 ③md-hub 前置三重防拉起风暴：unit 非 failed/active + **租约键缺席**（hub:lease 在=另一实例在位，让位语义）+ **维护标记** `quant:maintenance:md-hub` 键在场则跳过（人工停机窗口） |
| `server/scripts/systemd/49-quant.rules` | 修改 | polkit 增 `quant-md-hub@*.service` start/stop/reset-failed 放行（quant 用户）——与 reconciler 执行身份对齐 |
| `server/scripts/systemd/quant-md-hub@.service` | 修改 | `Restart=always`→`on-failure`；加 `RestartPreventExitStatus=78`；加 `MemoryHigh=768M`（12 号 §2.5 文档已承诺未落地） |
| `server/tests/test_sa4_reconciler.py` | 修改 | 补 L3 测试：期望表三源归一/漂移拉起/退避窗口共键/md-hub 三重前置（租约在场跳过/维护标记跳过/在场不拉）/strategy@* 语义 |
| `docs/architecture/12-实盘稳定性设计.md` | 修改 | §2.8 L3 行更新（覆盖面从 live-task 扩至三源）；§2.5 MemoryHigh 落地标记 |

## 限定范围
不碰：引擎代码（批 4 域）、部署管道（批 3 域）、sa4_reconciler 的 L1 Failed 恢复段（现状保持）、beat 周期（300s 不变）。

## 设计决策

### D1：md-hub 的 desired state 语义——**常开 + 两重熔断**
- live_task/strategy 的期望来自 DB 行；md-hub 无 DB 行——**常开**（系统单例数据面，永远该在跑）
- 熔断①：**租约键** `hub:lease` 在场=另一实例持有 fencing → **跳过拉起**（让位语义——两实例对打比缺实例更糟）
- 熔断②：**维护标记** `quant:maintenance:md-hub` Valkey 键在场 → 跳过+告警一次（人工停机窗口：`valkey-cli -n 4 SET quant:maintenance:md-hub 1` 即冻结 L3 对 hub 的拉起，TTL 可选）
- 熔断③（隐含）：退避计数与 L1 共键 `quant:sa4:backoff:quant-md-hub@quant.service`——拉起失败指数退避，不自打脸

### D2：strategy@* 的期望语义——**enabled 且无 live_task 行**
废架构单元（strategy_config.enabled=true 但没有对应 live_task 行——旧部署残留/手动启动场景）。有 live_task 行的任务永远走 live-task@{tid} 新架构，strategy@* 不重复拉。

### D3：hub 单元 Restart 语义变更的知情差异
`always`→`on-failure`：hub 退出码 0（KeyboardInterrupt 正常停）不再自动拉起——**语义变化**：人工 `systemctl stop` 后 L3 会在 300s 内拉回（desired=常开）除非打维护标记。这条写进操作指导：**人工停 hub = 打维护标记或 systemctl mask**，裸 stop 会被 L3 视为漂移。

## 验收标准
1. `pytest tests/test_sa4_reconciler.py -q` 全绿（18 存量+新增 ≥8）；全量绿；分层绿
2. `grep -c 'quant-md-hub' scripts/systemd/49-quant.rules` ≥1（polkit 放行）
3. `grep 'Restart=\|RestartPreventExitStatus\|MemoryHigh' scripts/systemd/quant-md-hub@.service` 三行齐
4. **G4 演练**（盘外，真机）：①stop md-hub → ≤300s L3 自动拉起+告警 ②打维护标记→stop→300s 后仍在 dead（不拉）③删标记→拉回 ④strategy@* 漂移场景（如有废架构单元）
5. 一交易日无事故（含 hub 单元 Restart 语义变更后首个 crash/stop 路径——如无自然发生则彩排注入）

## mock 方式
reconciler 测试全 mock（subsystemctl/PG/Valkey 打桩——现有 test_sa4_reconciler 范式）；G4 演练真机。

## 实施切分
单段一批（改动集中 tasks.py+两配置文件+测试，~200 行净增）。

## 参考文档
1. `docs/architecture/12-实盘稳定性设计.md` §2.5/§2.8/§2.10
2. `server/src/scheduler/tasks.py` sa4_reconciler 现段（L1 1195-1276 + L3 1277-1316）
