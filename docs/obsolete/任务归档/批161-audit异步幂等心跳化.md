# 批 161 · dep_audit 异步幂等心跳化（已实施终态）

> 立项+实施 2026-11-01，prod 验证 2026-11-02（05:00 自然轮首跑全绿）。
> 用户设计原则立法：**耗时不固定的任务一律异步+监控进展+僵死才杀+幂等续跑，不人为设时限**；分段只因资源（下载量/磁盘）非时间。本批=该原则首个系统化应用。

## 一、终态架构（全部已上产，releases/202611012311-80067eb）

### 1.1 时限拆除
装饰器显式 `soft_time_limit=172800`（48h 名义保命值，永不日常触达）——**非 None**（celery 5.6.3+billiard 下 task 级 None 回落全局 300s，双盲审 venv 源码铁证：request.py:366 + pool.py:803）。真实活性控制=心跳三态。

### 1.2 单线程全量+按批落库（幂等基座）
- ThreadPoolExecutor 与 1/7 抽签已删。354 包串行，坏网 30-60min 心跳在动即安全。
- **25 包一批** `cursor.executemany` upsert `security_component`（批=断点粒度，幂等覆盖写，逐批 commit——D18 无长事务）。
- **judge 随行落列**（judge_class/judge_why 在 audit 侧算好入库）——scan 直接用，消双份构造。
- **续跑判据**：`last_seen >= now-26h` 的包跳网查（26h 窗跨零点成立；dev 实测 n_remote 150→0；prod 实测 354 包 6.4s 零网查）。
- **sec:pypi 缓存**：refresh 参数已删——恒读缓存，7d TTL 到期自然轮转。

### 1.3 心跳三态（僵死才杀的眼睛）
- Valkey `sec:dep_audit:beat`（ts+n，**TTL 26h** 跨判定窗）；每 25 包 journal INFO；完成写 `status=done`。
- 下轮入口三态：done=正常 / beat 停>10min=zombie（警告+重开）/ 无键=interrupted（警告+重开）/ running=**互斥 skip**（防双跑）。
- 心跳写失败连续≥3 次 logger.error+errors 追加（不静默致盲）。

### 1.4 契约瘦身+表为真源
- `sec:dep_audit` = 轮次摘要（run_id/ts/n/counts/prev_state/last_complete/os 段/constraints/dev_names/errors）。
- **rows 真源=security_component 表**；scan 读表+**checked_at<=last_complete.ts 屏草**（COALESCE 兼容历史行）——坏网长跑与 scan 重叠时截断到完整上一轮。
- 兼容键 `sec:dep_audit:rows`（TTL 8d）=回滚兼容窗，**批 162+ 删**。
- 兜底 fail-loud：表读失败+兼容键空 ⇒ raise degraded（防全表 unknown+假回落）；rows 空 ⇒ 跳过电平判定保持 alerted 原态。
- 新鲜度：契约 30h fresh 线（DEP_AUDIT_FRESH_H）不变。

### 1.5 OS 域真源
provider 正门：`detect_provider().list_installed()/upgrade_candidates()`（RpmDnf/DpkgApt/Unsupported 适配层）——OS 判定随行落 source='system' 行+SQL 5对5 参数（含 latest_version）。

## 二、验收结果（全过）

| 项 | 结果 |
|---|---|
| 单测 | 2953 全绿（批161 九测：六形态+mock 全链三行为测——contextmanager 桩） |
| dev 真跑 | 三态 interrupted→done 全识别；续跑 n_remote 150→0；3503 system 行复活 |
| staging 彩排 | ok=83 failed=0 |
| prod 上产 | 首撞 ssh 瞬断 rescue 零影响收场→重试 failed=0 |
| **prod 首跑（05:00 自然轮）** | **354 包 6.4s**（缓存+续跑双命中 n_remote=0）/心跳 13 行/prev_state=interrupted 正确/OS 690/零错误 |
| B156A | act=17<20 回落闭环（114→48→36→17）；alerted=false 锁存复位（state 键直读证实）；回落通知已发 |

## 三、回滚
纯代码批。兼容窗=契约 pip rows 惰性键（8d TTL 自然过期=自拆）；代码面 git revert+管道 rollback。

## 四、遗留（挂账）
- 兼容键 `sec:dep_audit:rows` 下批删除（8d TTL 过期后无读端）。
- `_os_repo_all`（tasks.py）成死函数可顺手删。

## 附：双盲审处置对照（设计期，v2 历史）

| 发现 | 级 | 处置 |
|---|---|---|
| None 豁免回落 300s（A/B 同撞，venv 源码铁证） | P0 | 显式 172800 唯一路径 |
| refresh 恒 True 自相矛盾（A/B 同撞） | P0 | 恒读缓存+TTL 轮转+表续跑 |
| 心跳 TTL 3600 蒸发+三态不分（A/B 同撞） | P0 | TTL 26h+done 终态+三态判定 |
| 回滚窗假回落告警（A） | P0 | 兼容键+零行跳电平 |
| OS 域幽灵 API 断链（步4 同撞） | P0 | provider 正门+SQL 5对5 |
| icm 输入断供（步4 B） | P0 | in_constraint_max 迁移回 |
| 契约兜底缺失（步4 B） | P0 | fail-loud+零行跳电平 |
| 半更新表无屏草（B） | P1 | last_complete.ts 截断 |
| group/dev/新鲜度线（B） | P1 | 契约保留映射+30h 线 |
| 续跑判据跨零点（步4 B） | P1 | 26h 窗 |
| 续跑重判降级（步4 A） | P1 | 读回 jc/jw 沿用 |
| checked_at 屏草列（步4 A） | P1 | flush 补写+COALESCE |
| 写放大 D18（A/B） | P1 | 25 包批 executemany |
| running 互斥缺失（步4 B） | P1 | skip+return skipped |
| beat 静默致盲（步4 B） | P1 | 失败计数留痕 |
| ok 判定反转（步4 A） | P1 | bool(pip or os) 地板 |
| 测试缩水（A/B 步4 同撞） | P2 | mock 全链三测补 |
| judge 双份构造（A） | P2 | 随行落列 |
| 死代码（B） | P2 | 随体替换消亡 |
