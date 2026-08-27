# 批5：L3 扩面 + polkit 同批 + hub 单元对齐 SA4 + desired state 归一（重排后第 5 批）

> 依据：12 号 §2.10（原批 4 内容不变）；八步法（本文件=步 1 产物）。
>
> **v2（2026-08-27 步 2 双盲审修订）**：A/B 双同"需修订后复审"（P0×2+P1×7）。
> P0-1 failed 态黑洞（三处机制均不管——目标 1 引用的 SEGV 实证未被修复）→ failed 态按 ExecMainStatus
> 区分 78/崩溃并扩 L1 模式随期望表泛化；P0-2 strategy_config 镜像实锤 2-3 行 enabled 无 live_task
> （部署首周期即拉废 runner）→ 判定源改 systemctl is-enabled 显式意图；P1 全项吸收（维护标记（db 号随 VALKEY_URL）/
> .rules 手工通道显式化/回滚段/exit 1-5 矩阵+RestartPreventExitStatus=3 4/退避衰减泛化/Valkey
> 不可达 fail-closed/polkit 239 不分动词）。
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
| `server/src/scheduler/tasks.py` | 修改 sa4_reconciler | ①期望表：`_desired_units(conn)` 返回 `[(unit, source)]`——live_task running→live-task@{tid}；**strategy@* 按 `systemctl is-enabled` 显式意图**（v2 D2）；md-hub→常开 ②调和循环统一消费期望表（active+failed 双态调和——failed 按 ExecMainStatus 区分 78/崩溃，v2 D1）③md-hub 三重熔断：租约键（Valkey 不可达 fail-closed）+维护标记（db 号随 VALKEY_URL）+退避共键；stable-clear 随期望表泛化 ④`_sa4_units` 模式泛化三源 |
| `server/scripts/systemd/49-quant.rules` | 修改 | polkit 增 `quant-md-hub@*.service` manage-units 放行（quant 用户；al8 systemd 239 **不分动词**——一放即该单元全部管理动作，v2 纠正动词措辞）。web-api（同 quant）间接受益可 stop hub=数据面 DoS，与既有 live-task stop 权限同量级，知情接受 |
| `server/scripts/systemd/quant-md-hub@.service` | 修改 | `Restart=always`→`on-failure`；`RestartPreventExitStatus=78 3 4`（v2 扩 D3 矩阵）；加 `MemoryHigh=768M` |
| `server/tests/test_sa4_reconciler.py` | 修改 | 补 L3 测试 ≥12（v2 扩）：期望表三源归一/漂移拉起/退避共键+stable-clear 泛化/md-hub 三重前置（租约在场/维护标记/在场不拉）/**Valkey 不可达 fail-closed**/**failed 78 不拉 vs 崩溃拉**（ExecMainStatus 区分）/strategy@* is-enabled 门槛（enabled DB 行不拉）/租约 30s 残留跳过不写退避。`_sa4_units` 签名泛化后存量 18 测 mock 形状同步 |
| `docs/architecture/12-实盘稳定性设计.md` | 修改 | §2.8 L3 行更新（覆盖面从 live-task 扩至三源）；§2.5 MemoryHigh 落地标记 |

## 限定范围
不碰：引擎代码（批 4 域）、部署管道（批 3 域）、beat 周期（300s 不变）。
**v2 解冻一处**：`_sa4_units` 扫描模式随期望表泛化（live-task+md-hub+strategy 三源）——L1 Failed 恢复的退避/清零逻辑不变，仅扩扫描面（P0-1 根修的必要传导）。

## 设计决策

### D1：md-hub 的 desired state 语义——**常开 + 三重熔断（v2：failed 态归位 L3 + Valkey fail-closed）**
- live_task/strategy 的期望来自 DB 行；md-hub 无 DB 行——**常开**（系统单例数据面，永远该在跑）
- 熔断①：**租约键** `hub:lease` 在场=另一实例持有 fencing → 跳过拉起（让位语义）。**Valkey 不可达=fail-closed**（跳过+告警——分区时 fail-open 拉第二实例会短暂破坏 fencing）
- 熔断②：**维护标记** `quant:maintenance:md-hub` Valkey 键在场 → 跳过+告警一次。**db 号以 shared/.env VALKEY_URL 实时解析为准（产=4，本地/彩排=0——复盘盲审 P1 修正：v2 曾误写死 db0，照抄即哑炮=维护窗内 L3 照拉）**：`valkey-cli -u "$(grep VALKEY_URL shared/.env | cut -d= -f2-)" SET quant:maintenance:md-hub 1 EX 14400`（默认 TTL 4h 防遗忘=hub 长期裸奔）
- 熔断③：退避计数与 L1 共键 `quant:sa4:backoff:quant-md-hub@quant.service`；**stable-clear 循环随期望表泛化**（v2 修：原只扫 live-task 模式——hub 稳定跑 10min 后计数不清零，只挂一次也会被退避拖到 3600s）
- **failed 态归位（v2 P0-1 根修）**：L3 对 failed 态不再排除——按 `ExecMainStatus` 区分：`=78`（EX_CONFIG 配置错）跳过+告警人工；**其他（含 StartLimit 崩溃）→ reset-failed+start 走同一套三重熔断+退避**。L1 的 `_sa4_units` 模式随期望表一并泛化（`quant-live-task@*` + `quant-md-hub@*` + `quant-strategy@*`），限定范围解冻此一处
- **L1/L3 对 failed 的职责边界（v2.1 复核 B 定）**：L1 段只处理 **live-task** 的 Failed（现状不动——它有完整的退避/清零/已停检查）；**md-hub/strategy 的 failed 归 L3 段处理**（走三重熔断+ExecMainStatus 区分）。共享退避键幂等防双拉：先到者写计数，后到者退避窗内跳过

### D2：strategy@* 的期望语义——**v2 改判：systemctl is-enabled 显式意图（镜像实锤否决原案）**
- **原案被否决**：双盲 A/B 各自查镜像库实锤——`strategy_config` 有 2-3 行 `enabled=true` 无 live_task 关联（editest/livetest/test-live-pipeline），**原规则部署首周期即拉起废 runner**（XTP 400-600M/个，1.8G 生产机容量风险；backtest_verified=f 只挡下单不挡进程）
- **v2 定案**：判定源=**`systemctl is-enabled quant-strategy@{id}` 且无 live_task 关联**（v2.1 复核 B 补去重护栏——is-enabled 与 live_task 并存时防双拉；废架构单元没人 enable 过，零误拉）。enabled DB 行不再作为拉起依据，仅作信息注记
- systemctl 侧残留同步审计（v2.1 补）：`systemctl list-unit-files 'quant-strategy@*'`——与 DB 审计互为镜像，"没人 enable 过"须实证非假设
- **部署前置审计步骤**（v2 新增，G4 演练①前置）：
  ```sql
  SELECT id FROM strategy_config WHERE enabled AND id NOT IN (SELECT strategy_id FROM live_task WHERE strategy_id IS NOT NULL);
  ```
  输出须人工核认（预期=测试残留行）；对每行决定 disable 或留观察。**首周期验证零 strategy 单元被拉**（G4 加一条）

### D3：hub 单元 Restart 语义变更——**v2 补退出路径矩阵 + RestartPreventExitStatus 扩展**
`always`→`on-failure` + `RestartPreventExitStatus=78 3 4`（v2 扩：3=让位 4=租约重试耗尽——on-failure 下照样拉，30s 后再抢租约→5 次打穿 StartLimit 进 failed 黑洞；不抢=交 L3 300s 轮询在租约释放后接管，与 desired-state 语义自洽）。

**退出路径×行为矩阵（v2 补全，双盲 B 指原稿仅 exit 0 一行）**：
| 退出 | systemd（on-failure+Prevent=78,3,4） | 语义 |
|---|---|---|
| 0 KeyboardInterrupt | 不拉 | 正常停（always 也不对抗显式 stop——v2 修正原概念错误） |
| 1 vnpy 缺/事件线程死 | **拉** | 进程域故障，重启正确 |
| 3 让位 | **拉**（v2.2 修：Prevent 只留 78——含 3 时部署波次必炸，staging 实锤） | RestartSec 30s 等租约过期后重试 |
| 4 租约重试耗尽 | **拉**（v2.2 同修） | 同上 |
| 5 续租丢 | **拉** | "systemd 将接管"告警文案即此意 |
| 78 EX_CONFIG | **不拉**（Prevent） | 配置错 Failed 告警人工 |
| StartLimit 打穿 | failed→**L3 按 ExecMainStatus 区分**（D1 v2） | SEGV 同款形态不再无人管 |

**知情差异**：人工 `systemctl stop` 后 L3 在 300s 内拉回（desired=常开）——**这是 L3 新增行为，与单元 Restart 变更无关**（v2 修正原混淆）。操作纪律写进操作指导：**人工停 hub = 打维护标记或 systemctl mask**。

## 验收标准
1. `pytest tests/test_sa4_reconciler.py -q` 全绿（18 存量+新增 ≥12）；全量绿；分层绿
2. `grep -c 'quant-md-hub' scripts/systemd/49-quant.rules` ≥1（polkit 放行）
3. `grep 'Restart=\|RestartPreventExitStatus\|MemoryHigh' scripts/systemd/quant-md-hub@.service` 三行齐（Prevent=`78`，v2.2 定稿——含 3/4 时部署波次必炸，staging 实锤）
3b. **.rules 服务器侧实装**（v2 P1-2：管道不覆盖——quant-install-units 只 glob *.service，.rules 无自动通道）：
   ```bash
   # michael 通道手工（文件头自述同款）
   scp server/scripts/systemd/49-quant.rules michael@120.24.235.98:~/3b2/
   ssh michael@120.24.235.98 'sudo cp ~/3b2/49-quant.rules /etc/polkit-1/rules.d/ && sudo systemctl restart polkit'
   # 验收（服务器侧，不是仓库 grep；v2.1 复核 A 修正：quant-svc 走 sudoers=root 通道
   # ——root 调 systemd 不触发 polkit，测不到放行！须以 quant 身份直跑 systemctl）：
   sudo -u quant systemctl start quant-md-hub@quant.service && echo 'polkit 放行实测过'
   # （若 sudoers 拒 quant→root systemctl：经 celery 触发一次 reconciler 实测同等效力）
   ```
4. **G4 演练**（盘外，真机，v2 扩七步）：
   ① 前置审计（D2 SQL）+首周期验证零 strategy 单元被拉
   ② stop md-hub → ≤300s L3 自动拉起+告警
   ③ 打维护标记（db 号以 VALKEY_URL 解析为准，产=4）→ **打完必须 EXISTS 复验才许 stop** → stop → 300s 后仍 dead（不拉）→ 删标记 → 拉回
   ④ **failed 态演练**（v2 P0-1 验收）：
      a. 崩溃 failed：注入坏入口（临时 ExecStart 指向不存在模块 60s 窗）→ StartLimit → failed → ≤300s L3 reset-failed+拉起
      b. 78 failed：quant-svc start 前置设 `systemctl set-property` 或临时 wrapper 退出 78（简单法：`systemctl start quant-sbx-78probe@tmp` 一次性单元 ExecStart=/bin/sh -c 'exit 78' RestartPreventExitStatus=78 → failed）→ L3 不拉+告警人工
   ⑤ 一交易日无事故
5. 一交易日无事故（含 hub 单元 Restart 语义变更后首个 crash/stop 路径——如无自然发生则彩排注入）

### 回滚（v2 补——四要素闭合；双盲 A/B 同指缺失）
- **代码**（tasks.py）：批 3 管道自动——revert+重发布（波及 celery-risk 波重启）
- **单元**（.service）：旧 release 文件恢复+daemon-reload（quant-install-units 以旧 release_id 重跑；Restart 变更不影响运行中进程，回滚窗安全）
- **polkit**（.rules）：手工恢复旧 49-quant.rules 至 /etc/polkit-1/rules.d/ + `systemctl restart polkit`（不热加载——2026-08-17 踩坑）
- **Valkey 残留**：退避计数键+维护标记清理（`DEL quant:sa4:backoff:quant-md-hub@quant.service quant:maintenance:md-hub`）
- **误拉起处置**（如 D2 意外触发）：`systemctl stop+reset-failed quant-strategy@{id}` + 禁用 DB 行

## mock 方式
reconciler 测试全 mock（subsystemctl/PG/Valkey 打桩——现有 test_sa4_reconciler 范式）；G4 演练真机。

## 实施切分
单段一批（改动集中 tasks.py+两配置文件+测试，~200 行净增）。

## 参考文档
1. `docs/architecture/12-实盘稳定性设计.md` §2.5/§2.8/§2.10
2. `server/src/scheduler/tasks.py` sa4_reconciler 现段（L1 1195-1276 + L3 1277-1316）
