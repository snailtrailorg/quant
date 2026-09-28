# 批 78 · 系统监控页 CPU 卡+曲线 FIR 滤波（反馈池 #16 追加扩围）— **v2（双盲全处置）**

> 立项 2026-09-28。批号说明：总方案 v4 批次 71-77 为预留段（编号不动，防编号灾难重演），本批为反馈池插入批取空闲号 **78** 先行。
> 用户裁定已录（2026-09-28）：三卡组统一 4 列｜CPU 卡对齐现有卡模式（曲线+阈值告警全链）｜**FIR=矩形窗 5 点滑动平均**｜**卡片大数字=真值，曲线=滤波值，告警阈值=真值**｜**阈值默认 warn 0.70 / crit 0.90**（知情裁定：0.7 档盘中瞬时越限告警属预期可能）｜**词条经文案师审读**。
> **v2 修订**：方案双盲审 A/B（P0×2+P1×9+P2×15，同判综合）全处置——逐条落点见 §九。

## 一、目标

系统监控页（Observe.vue 运行状态页签）三卡组每行固定 4 张卡；资源消耗组最前插入 **CPU 占用率卡**（与 mem/disk 卡完全同构：sparkline+阈值告警）；**所有曲线项目**（含 cpu）的落库值改为 5 分钟矩形窗 FIR 滤波值；曲线 x 轴标签改纯日期 `mm-dd` 且刻度钉日界（minInterval 24h），按日期交替背景带（日期界限视觉清晰）。

## 二、依赖（就绪）

- collector 采集框架 ✅（`collector.py:126-177`：_collect_mem/_swap/_disk + _cached_kind 节流；`_read_cfg` 由 `_CFG_KEYS`（:38-54，thresholds+periods 两映射）+ `_DEF_THRESHOLDS` 构建）
- monitor 30s 链 ✅（`monitor.py:240` run_check → `monitor.py:272` evaluate（**无 try 包裹——任何 KeyError=30s 链崩**）→ 告警 → `monitor.py:336-352` system_metric 每分钟落行，ON CONFLICT (ts) 对 PK）
- 阈值体系 ✅（evaluate `monitor.py:195-207`：`thr = snap.get("thresholds") or {mem,disk,swap} 兜底`；恢复豁免 res_evidence `monitor.py:297-299`）
- API ✅（`system.py:215-252` metrics()：最新行+7 天序列，60s `_METRICS_CACHE`；`/metrics`=`system.py:183-185` render_prometheus 走 collect() 即时快照；`/readyz`=`system.py:165` 同为 collect() 调用方）
- 配置注册表 ✅（**`SYSTEM_CONFIG_BOUNDS`** `system.py:20-33`；阈值对交叉校验 `_pairs` `system.py:517-519`；seed 惯例=`0076_resource_cfg.py:22-44`）
- 前端 ✅（`SystemMetricsCards.vue`：defs 驱动+VChart（`:34` use 按需注册——**markArea 须补注册**）+`:74` 共享映射 `p.total ? p.used/p.total : 0`+`:125` 本组件 card-grid；`ServiceStatusCards.vue:47`/`ConnectionCards.vue:61`）
- 迁移链 head=0111 ✅ → 本批 **0112**；system_metric 表=`0075_system_metric.py`（ts timestamptz PRIMARY KEY+6 bigint）

## 三、产出（文件清单）

**后端 5 文件**：

1. `server/migrations/versions/0112_metric_cpu_fir.py`（新）：
   - system_metric 加 5 列（**DOUBLE PRECISION**，全 NULL 允许）：`cpu_used`、`mem_used_avg`、`swap_used_avg`、`disk_used_avg`、`cpu_used_avg`；downgrade 对称 drop。纯 ADD COLUMN=非破坏性，不触发 DDL 门。
   - **seed 三键**（0076 惯例 INSERT…ON CONFLICT (key) DO NOTHING，downgrade DELETE）：`alert_cpu_warn=0.70`/`alert_cpu_crit=0.90`/`collect_period_cpu=30`——不 seed 则设置页永不出现、POST 404。
2. `server/src/health_monitor/collector.py`：
   - `_collect_cpu()`：`psutil.cpu_percent(interval=0.1)/100` → `{"pct": 0-1}`；`_collect_resources`/`collect` 组装 `resources.cpu`（无 total/used 字节）。
   - **阈值链（盲审 P0-2）**：`_DEF_THRESHOLDS` + `_CFG_KEYS["thresholds"]` 补 `alert_cpu_warn/crit`、`_CFG_KEYS["periods"]` 补 `collect_period_cpu`——缺任一=thr["cpu"] KeyError→30s 链崩。
   - **render_prometheus（盲审 P0-1）**：`:413-418` resources 循环对 cpu **分支只 emit `quant_res_cpu_pct`**，不 emit used/total bytes（cpu dict 无此二键，现行为硬下标必 KeyError→/metrics 500）。
3. `server/src/health_monitor/monitor.py`：
   - evaluate：+`cpu_high` 规则（component=`"cpu"`）+ **`:195-197` 兜底 dict 补 cpu**（synthetic snap 无 thresholds 时同 KeyError 面）+ `res_evidence` 补 `cpu_high`。
   - 落库段（`:342`）：INSERT 前查最近 4 行 used 值——**per-kind `WHERE xxx_used IS NOT NULL`**（均值语义钉死），算**含当前行的 5 点均值**（不足 5 行用可得行全量平均），INSERT 扩 12 列。
4. `server/src/web_api/routes/system.py`：
   - **`SYSTEM_CONFIG_BOUNDS`** +`collect_period_cpu=(30,...)`/`alert_cpu_warn=(0,1,...)`/`alert_cpu_crit=(0,1,...)`；**`_pairs` 补 alert_cpu warn↔crit**（缺=写侧可落 warn≥crit 分级反转）。
   - metrics()：两 SELECT 扩 5 列；`resources.cpu={"pct"}`；`series[kind][].used_avg`；**`series.cpu` 后端只回 `cpu_used IS NOT NULL` 行**（旧行不进序列，免疫前端 null 断点）。
5. `docs/architecture/模块契约/health_monitor.md`（回写）：collect 形状（resources.cpu）+render_prometheus cpu 分支+system_metric 写路径（**批 22 起漏记，本批补上**）+12 列+cpu_high 规则。

**前端 4 文件**：

6. `web/src/components/SystemMetricsCards.vue`：
   - defs 首位 +`{kind:'cpu'}`（卡序 **CPU→内存→交换→磁盘**，批 28-6 序前置修正）；rule 映射 cpu→cpu_high；cpu 卡 bytes 显示 `—`。
   - **`:125` 本组件 `.card-grid` repeat(3→4)**（v1 漏项，盲审 B P1-3 抓出）。
   - **`:34` use 补 `MarkAreaComponent`**（不注册=echarts tree-shaking 静默不渲染）。
   - **曲线数据**：四卡统一 `p.used_avg ?? p.used` 回退（含 cpu——v1「cpu 用 p.used」自相矛盾已纠正，盲审双同）；**cpu 显式分支不走 `p.total ? used/total : 0` 共享映射**（无 total 恒 0 平线陷阱），cpu 序列值=pct 本身。
   - xAxis：`formatter: '{MM}-{dd}'` + **`minInterval: 24*3600*1000` 钉日界**（hideOverlap 只避让重叠不去同文本重复——12h 档会「09-21 09-21」并列）。
   - markArea 日期交替带：按序列日期边界切区间（≤8 段），**用 tokens.css 实存令牌**（编码时选定，如 `--bg-canvas`——v1 虚构 `--fill-weak` 已纠正），silent。
7. `web/src/components/ServiceStatusCards.vue`——`.card-grid` repeat(3→4)。
8. `web/src/components/ConnectionCards.vue`——同上。
9. `web/src/locales/`（sysmon 域）——`cpu` 词条 zh/en（**文案师智能体产出**，结果落 locales；键结构编码时对齐现有 sysmon.mem 等）。

**测试**：tests/ 补——cpu 采集（mock psutil.cpu_percent）/5 点均值含部分窗口与 per-kind NULL 过滤/cpu_high 阈值判定+恢复豁免+兜底 dict/落库 12 列 SQL 形状/metrics() 返回形状（cpu+used_avg+series.cpu 过滤）/**/metrics 200 且含 quant_res_cpu_pct 族**（现有 test_health_monitor.py:243 真实 collect 会自动拦 P0-1，补断言）。

## 四、限定范围

只改上列文件。**不碰**：告警分发链、其他 health 规则、部署管道与波次清单（无新进程新单元）、Observe.vue 页签结构、smoke-web.sh（**已核实其端点清单不含 /api/system/metrics——机器级形状断言由上述 pytest 兜，非 smoke**）。批 51「固定 3 列」与批 28-6 卡序两旧裁定随本批更新（待办.md 勾稽）。

## 五、接口契约

- 新加：`_collect_cpu() -> dict | None`（`{"pct": float∈[0,1]}`，异常返 None 同 mem 形状）。
- 变更：system_metric INSERT 列 7→12；metrics() 返回加字段不删字段（唯一消费方=SystemMetricsCards.vue，api.js:142 全仓唯一引用，实证向后兼容）；render_prometheus 对 cpu 单族 emit（行为=新增 quant_res_cpu_pct 族，used/total 族不含 cpu）。
- 现有消费方核对：collect() 调用方全集=beat health_monitor_check（跑 quant-celery-risk -c 1）、/metrics、/readyz、/api/health/components——100ms 阻塞×30s 节流一次，四调用方共容（_cached_kind 进程内节流）。

## 六、制度符合性（W6）

- 迁移走 alembic 正常通道（非破坏性 ADD COLUMN+数据 seed，不走 allow_contract）✓
- 无新进程/单元/wrapper/波次改动（CPU 并入 health_monitor_check 既有链）✓
- N 语言架构：词条入注册表 ✓；**用户可见词条经文案师**（用户裁定）✓
- 配置存放立法：三新键入 system_config（BOUNDS+seed），非 .env ✓
- UI 两级验收：机器级=pytest 形状（smoke 不覆盖该端点，已述）；人工视觉=用户本人 ✓

## 七、验收标准

1. `pytest tests/ -q` 全绿（存量 1491+新增，零回归）+ pyflakes 零新增
2. 迁移：本地 dev 库 `alembic upgrade head` 后 5 新列在+`SELECT key,value FROM system_config WHERE key LIKE 'alert_cpu%' OR key='collect_period_cpu'` 3 行；downgrade 可逆
3. 前端门（W2）：`npm run build` 绿 + `bash scripts/smoke-web.sh` 绿（基线不回归）
4. 行为级：dev 环境起服 6 分钟后 `SELECT * FROM system_metric ORDER BY ts DESC LIMIT 6`——新行 cpu_used 与 4 个 avg 列非 NULL；页面 CPU 卡有曲线；**`curl -s localhost:8001/metrics | grep quant_res_cpu_pct` 有值**
5. 部署：staging 彩排绿 → prod 盘后窗；生产 5 分钟后新行 avg/cpu 非 NULL
6. 步 8 观察（09-29）：监控页正常+avg 曲线平滑；**cpu_high 若触发须归因记录**（0.70 档下盘中瞬时越限属用户知情裁定的预期可能，归因=真高 vs 阈值偏紧，不构成本批验收自败项）

## 八、风险面与回滚

- **单写者实证**（盲审 B）：beat 30s→risk 队列（expires=25 防堆积）→quant-celery-risk -c 1 独占——SELECT-INSERT 非原子在单写者下无害。
- **ON CONFLICT 表述修正**（盲审 A P2-1）：INSERT 用 `to_timestamp(浮点 ts)`，小数秒不同则 PK 理论上不冲突；现状防重实际靠 30s×60s 窗节拍单命中。**ts 取整分钟=既有行为变更，另批裁定，本批不动**。
- **新旧段过渡台阶**：边界行（原值 vs 5 点均值）差一个平滑幅，7 天留存自愈——非「无缝」，语义无误导。
- **interval=0.1 论据修正**（盲审 B P2-4）：`--max-tasks-per-child` 在通用 worker；health_monitor_check 跑 risk（-c 1）无此旗标；结论不变——阻塞 100ms×节流后四调用方共容。
- **DOUBLE PRECISION**（盲审 B P2-5）：disk TB 级均值若 REAL=float4 有 ~百 KB 绝对误差——显示无感但零成本改双精度。
- **回滚**：整版本 rollback（旧代码不写新列=DEFAULT NULL 向后安全）+ 迁移 downgrade 可逆（drop 列+DELETE seed 键）；两向独立可执行。

## 九、v2 双盲处置对照（A/B 报告逐条落点）

| # | 级 | 发现（审稿人） | 处置 |
|---|---|---|---|
| 1 | P0 | render_prometheus 硬下标 used/total，cpu 并入即 /metrics 500（A+B 同） | §三.2 分支单族 emit+§三 测试补 200 断言 |
| 2 | P0/P1 | 阈值链三处漏步（_DEF_THRESHOLDS/_CFG_KEYS/兜底 dict）——evaluate 无 try，30s 链崩（A 定 P0） | §三.2/§三.3 三处补齐+测试 |
| 3 | P1 | CONFIG_DEFAULTS 错名（A+B 同） | §二/§三.4 正名 SYSTEM_CONFIG_BOUNDS |
| 4 | P1 | 迁移不 seed 三键=设置页死键（A+B 同） | §三.1 seed+downgrade DELETE（0076 惯例） |
| 5 | P1 | _pairs 交叉校验漏列（B） | §三.4 补 alert_cpu warn↔crit |
| 6 | P1 | SystemMetricsCards 自身 card-grid 漏改（B——v1 真实漏项） | §三.6 :125 3→4 |
| 7 | P1 | MarkAreaComponent 未注册=静默不渲染（A+B 同） | §三.6 use 补注册 |
| 8 | P1 | cpu 曲线自相矛盾/死列（A+B 同） | §三.6 四卡统一 used_avg 回退 |
| 9 | P1 | mm-dd 同日堆叠，hideOverlap 不够（B；A 定 P2） | §三.6 minInterval 24h |
| 10 | P1/P2 | 阈值默认自造+验收 6 自败（B；A 建议 0.85） | **用户裁定 0.70/0.90**+§七.6 改归因制 |
| 11 | P2 | --fill-weak 令牌不存在（A+B 同） | §三.6 实存令牌编码时选定 |
| 12 | P2 | component 名钉死（B） | §三.3 "cpu" |
| 13 | P2 | series.cpu 旧行 null（B） | §三.4 后端过滤 IS NOT NULL |
| 14 | P2 | 均值 per-kind NULL 语义（A） | §三.3 WHERE IS NOT NULL |
| 15 | P2 | smoke 不覆盖该端点（A+B 同） | §四/§七.3 改口 pytest 兜 |
| 16 | P2 | 契约漂移（A+B 同） | §三.5 回写 health_monitor.md |
| 17 | P2 | 文案师豁免须用户点头（A） | **用户裁定走文案师**（已并行） |
| 18 | P2 | /readyz 调用方漏盘（B） | §五 补述 |
| 19 | P2 | ON CONFLICT 失真（A） | §八 改口+另批不动 |
| 20 | P2 | REAL→DOUBLE（B） | §三.1 采纳 |
| 21 | P2 | 过渡「自然」表述乐观（A） | §八 改口「台阶，7 天自愈」 |
| 22 | P2 | interval 论据错位（B） | §八 论据修正 |
| 23 | P2 | 锚点微偏 res_evidence 297-299/metrics 215-252（B） | §二 已正 |
| 24 | **P0 存量** | **同车裁定**（用户 2026-09-28）：`_dispatch_async` def 6 参 vs `_submit` 七元组 put（批 44 半修）——worker 消费必 TypeError 被吞，**外推链（IM/邮件/短信）09-18 批 47 上产起全断**；broadcast skip_level 门槛语义从未生效。行为级实锤：dev 直调 run_check 现场抓 `takes 6 positional arguments but 7 were given` | `dispatch.py` def 补 `skip_level: bool=False`+`matched` 过滤 `skip_level or` 短路（**两行**）；钉两条：`test_submit_to_worker_chain_no_typeerror`（真穿 _submit→_q→worker，禁 mock 绿）+`test_skip_level_bypasses_channel_min_level`（门槛语义）；**范围变更**：§四「不碰告警分发链」对本条豁免（W6 显式交代）；既有测试全 6 参直调恰好绕开真形状=mock 绿掩盖又一例 |

## §九 补：代码双盲审（步 4）处置

- A/B 双同：无 P0；**P1 双同=落库段零测试覆盖** → 补三钉（INSERT 12 列参数逐位+SELECT per-kind IS NOT NULL/LIMIT 4+恢复豁免 D-F5）
- A/B P2 处置：:243 HTTP 级 cpu 断言 ✓／契约补 R8-R10 规则族（含 cpu_high）✓／metrics 测试缓存重置 ✓／变量遮蔽 r→row ✓／dayBands ≤8 段=7d 窗天然满足（不另设限）／过渡窗 cpu 卡 60s 假 0=知悉（sparkline 侧正确显示无数据）
- 顺手：smoke-web.sh「切换会话列表」断言对齐批 70 M6 退役 404 预期（批 70 漏更的陈旧断言，非本批引入——24 绿 0 红收口）
- 行为级（A P2-4 问询项）已补做：dev 直调 run_check 落库 12 列全非 NULL（cpu_used=0.05+四 avg，部分窗口语义）+/metrics `quant_res_cpu_pct` 族在位

## 参考（≤2）

- `docs/architecture/模块契约/health_monitor.md`（本批回写对象）
- `flow/待办.md` 反馈池 #16（需求源+裁定链）
