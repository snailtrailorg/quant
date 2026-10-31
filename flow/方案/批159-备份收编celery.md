# 批 159 · 备份收编 celery（v2 · 双盲审 A/B 全处置后重写）

> 立项 2026-10-31，用户裁定「正规收编，不能用链接凑合」。v2 同日：A（1P0/4P1/2P2）+B（2P0/3P1/2P2）双盲审全处置——两审独立同撞：管道 rc 陷阱（P0）、notify 字面量（P1）、时区措辞。处置对照见文末。
> 关联：批 83b（beat 收编先例）；批 110（时限显式）；A06 §11（cron 面条目随本批销账）。

## 一、为什么收编

cron 是管道外调度面：脚本不跟 release 翻链（10-31 盲点实证）、无 journal/时限/告警、失败静默。备份失败该有告警——这就是平台能力，按判据归 celery。cron 面清零=调度单面化。

## 二、设计

### 2.1 新任务 backup_db（tasks.py）——契约钉死四道防线

1. **管道双 rc 校验**（A-P0/B-P0 同撞）：Popen 显式双进程（pg_dump.stdout 接 gzip.stdin），两个 returncode 都查，任一非 0=失败——单测必须含「pg_dump rc≠0 而 gzip rc=0」反例（管道 rc=末命令的陷阱）。
2. **残件防线**（B-P0）：写 `$FILE.tmp`，双 rc 全 0 才 os.replace 原子转正；retention glob 只认正式名（tmp 不占份）；失败/软限路径 rm partial + killpg（subprocess start_new_session=True，SoftTimeLimitExceeded 余量 300s 内杀进程组防孤儿续写）；任务入口先清历史孤儿 tmp。
3. **流式写盘**（A-P1）：pg_dump 管道直写文件句柄，**禁 capture_output**（原始 ~10G 级会顶爆 cgroup MemoryMax=1600M 连杀整池）。
4. **空间预检**（B-P1）：dump 前查目标盘可用 < max(2×上份 dump 大小, 1G) ⇒ abort + 告警，不启动。

- 保留策略：KEEP_COUNT=3（用户裁定），文件名时间戳降序删旧（首轮自动把现存 ~6 份裁到 3）。
- 时限：bind=True，soft=1800/hard=2100（批 110 清单登记；软限=killpg+rm 的唯一执行窗，300s 余量）。
- 失败告警：`notify("critical", "system", "数据库备份失败", code="backup.failed")`（A/B 同撞：Literal 无 "crit"）——**PG 停时告警可达性**：dispatch 外推走 alerts 队列不依赖被备份库，验收须实测。
- 成功：journal INFO（大小+清理明细）。

### 2.2 beat 条目（app.py）

- "db-backup": crontab(hour=2, minute=0)，queue=data，**expires=3600**（停机恢复后旧消息丢弃——深夜补跑无价值；A-P2）。
- 时区语义（A/B 同撞修正）：beat=app.conf timezone="Asia/Shanghai"（**配置值非 OS 时区**，二者当前恰好一致）；上产前 timedatectl 核实 OS tz 一致（验收项）。
- 双跑无害声明（A-P2/B-P2）：文件名秒级时间戳互不覆写、retention 幂等——cron 未删窗口内同夜双份属预期。
- 顺手更正两处陈旧注释：notifications-cleanup/logs-cleanup 的「避 04:00 备份」→ 02:00（A-P2：仓库民俗错记备份在 04:00）。

### 2.3 退役面（硬门时序，B-P2-1）

实施第一步：**上服务器核实 crontab 实际行**（A-P1-5：server-info.md 已被我改成 release 树路径，但 crontab 现实未核实——两文档矛盾须先定谳）。

退役硬门（顺序强制）：上产 → 02:00 首轮 celery 自动跑**成功实证**（journal 成功行+backups 新文件+恰 3 份）→ michael 删 crontab 行 → 删 /home/quant/scripts/backup-db.sh 装位 → 仓内 server/scripts/backup-db.sh 删除（真源移交）。

## 三、限定范围

改：tasks.py（新任务四道防线）、app.py（beat 行+两处注释更正）、server-info.md、INSTALL.md:252、docs/architecture/A01:283（B-P1-3 引用面：删真源必扫全仓——两处还指旧脚本+旧 7 天）、tests（管道双 rc 含反例/残件清理/空间预检/保留 3 份四套）。
不碰：backup-restore-drill.sh 逻辑（:21 注释「期望 backup-db.sh 产出」一句改指任务产物）。

## 四、验收

1. 单测四套：双 rc 含「gzip rc=0 掩盖 pg_dump 失败」反例；失败后无 partial 残留；空间不足 abort+告警；首轮 6→3 份。
2. dev：手动触发 → backups 新文件+份数正确+journal 行。
3. prod 硬门：手动触发成功 → **PG 停模拟下 IM/邮件告警至少一路可达** → 02:00 首轮自动成功+恰 3 份 → michael 删 cron（当日完成）→ server-info/INSTALL/A01 三处与现实一致。

## 盲审处置对照

| 发现 | 级 | 处置 |
|---|---|---|
| 管道 rc 陷阱（截断 dump 入保留集） | P0（A/B 同撞） | 双 Popen 双 rc+反例单测（§2.1.1） |
| 残件腐蚀保留集+孤儿进程 | P0（B） | tmp+原子转正+killpg+入口清孤儿（§2.1.2） |
| capture_output OOM 连杀 cgroup | P1（A） | 流式写盘契约禁 capture（§2.1.3） |
| notify("crit") 非法+PG 停可达性 | P1（A/B 同撞） | "critical"+code+停库实测（§2.1） |
| 空间预检缺失（78% 盘） | P1（B） | 2× 上份/fallback 1G 闸（§2.1.4） |
| crontab 现实矛盾（server-info vs 实际） | P1（A） | 实施首步核实（§2.3） |
| 引用面 INSTALL/A01 未扫 | P1（B） | 限定范围补全（§三） |
| 时区措辞（conf 非 OS） | P1/P2（A/B 同撞） | 引用配置事实+核实项（§2.2） |
| expires/双跑声明/04:00 注释 | P2（A/B） | 全采纳（§2.2） |
| 删除时序硬门 | P2（B） | 首轮成功实证为删 cron 前置（§2.3） |
