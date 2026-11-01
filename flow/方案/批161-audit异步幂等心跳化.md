# 批 161 · dep_audit 异步幂等心跳化（v2 · 双盲审 A/B 全处置后重写）

> 立项 2026-11-01。用户设计原则立法：**耗时不固定的任务一律异步+监控进展+僵死才杀+幂等续跑，不人为设时限**；分段只因资源（下载量/磁盘）非时间。
> v2 同日：A（P0×4/P1×3）+B（P0×2/P1×4/P2×2）双盲审全处置——**三核心 P0 双审独立同撞**（None 豁免假/refresh 自相矛盾/心跳 TTL 蒸发），处置对照见文末。

## 一、病根（现状四病，不变）

时限悬崖（1500s）/整轮重跑/零进展可见性/多线程复杂度（时限拆除后唯一理由消失）。

## 二、设计 v2

### 2.1 时限拆除的唯一可行路径（A/B 同撞 P0 处置）

celery 5.6.3+prefork 下 task 级 soft_time_limit=None **回落池级全局 300s**（billiard/pool.py:803 铁证）——非豁免。唯一路径=装饰器显式大值：**soft_time_limit=172800 / time_limit=172800**（48h 名义值——语义是"永不日常触达"，心跳+僵死判定才是真控制器；注释指向本方案）。批 110 清单登记为"名义保命值，真实控制=心跳"。

### 2.2 单线程全量 + 按批落库（幂等基座）

- 删 ThreadPoolExecutor 与 1/7 抽签（冗余设计退役——TTL 自然轮转替代）。
- **sec:pypi 缓存：refresh 恒 False**（参数删除）——7d TTL 到期自然重查=天然轮转；缓存承担跨轮新鲜度+续跑加速双角色（B-P0 处置：恒 True 则永不读缓存，自相矛盾）。
- **续跑判据=表**：本轮已刷 last_seen（>今日 05:00 起）的包**跳过网络查询**（缓存+表双命中）；中断重跑只网查未完成部分。
- **25 包一批 executemany 落库**（A-P1 处置：D18 批量规范与断点粒度折衷；批界=断点粒度，幂等覆盖写不变）；逐批 commit（禁跨轮单事务——idle_tx 红线）。

### 2.3 心跳与三态判定（B-P1/A-P0 处置）

- 每包完成刷 Valkey `sec:dep_audit:beat`（ts+n，**TTL 26h**——跨过 24h 判定窗）；每 25 包 journal INFO。
- **正常完成写终态**：同键加 status=done（n=总数）。
- 下轮入口三态判定（纯函数，单测覆盖）：键有 done=正常；有 beat 且 ts 距今>10min=**僵死**（journal 警告+照常开新轮——幂等无损失）；两无=中断（同警告）。

### 2.4 摘要契约（兼容屏草+OS 域+group——B-P1×2/A-P0/P1 处置）

契约 sec:dep_audit 保留但瘦身：run_id/ts/n/counts + **last_complete**（最后完整轮 run_id+ts）+ **os 段全量**（installed/upgrades）+ **constraints/dev_names/group 映射**（小 JSON）+ **pip rows 惰性写入一个发布周期**（回滚兼容窗：旧 scan 读新契约不炸——A-P0 假回落告警根除；下批删除）。
- **scan 读表+轮次屏草**：dep_health rows 改读台账（checked_at <= last_complete.ts 截断——B-P1 半更新表根除）；零行时**跳过电平判定保持 alerted 原态**（A-P0 双保险）。
- **新鲜度线**：表 max(checked_at) 距今>30h ⇒ components degraded（沿用 DEP_AUDIT_FRESH_H）——audit 静默死不再表永远绿。
- OS 域：audit 将 os 判定随行落 source='system'（表复用现列）。

### 2.5 judge 随行落表（A-P2 采纳）

judge 在 audit 侧算好随批落 judge_class/judge_why 列（表已备）——scan 直接用，消 _gm/judged_map 双份构造。

### 2.6 顺带（B-P2）

删 tasks.py:984-997 sec:dep_alert_state 死代码块（update 后从未回写）。

## 三、限定范围

改：tasks.py（dep_audit 重构+死代码删）、security_collector.py（dep_health 读表+屏草+新鲜度+judge 直用）、os_pkg.py（refresh 参数删）、tests 六套（三态判定/批落库幂等/续跑跳网/屏草截断/新鲜度降级/None 兼容负例）。
不碰：judge 矩阵（160 刚修）、B156A 电平逻辑、beat 时刻、表结构（复用现列）。

## 四、验收

1. 单测六套（§三）。
2. dev：SIGSTOP 全链（挂起→beat 停→重触发入口判僵死警告→SIGCONT+revoke 清场；预期内 idle_tx 告警一条写明）；kill -9 中断→重触发续跑（表已批跳网）。
3. prod：自然轮观察——journal 心跳密度（每 25 包一行）+终态 done+电平正常；**不做** prod SIGSTOP（占池槽风险，dev 全链已覆盖——A/B 共同裁定）。
4. 批 110 清单：dep_audit 改注"名义 172800，真实控制=心跳（批161）"。

## 五、回滚

纯代码批。**兼容窗**=契约 pip rows 惰性写保留一个发布周期（回滚后旧 scan 可读）——下批（批162+）删。git revert+rollback 天然覆盖代码面。

## 盲审处置对照（v2 历史）

| 发现 | 级 | 处置 |
|---|---|---|
| None 豁免回落 300s（A/B 同撞，venv 源码铁证） | P0 | 显式 172800 唯一路径（§2.1） |
| refresh 恒 True 自相矛盾（A/B 同撞） | P0 | 恒 False+TTL 轮转+表续跑（§2.2） |
| 心跳 TTL 3600 蒸发+三态不分（A/B 同撞） | P0 | TTL 26h+done 终态+三态纯函数（§2.3） |
| 回滚窗假回落告警（A） | P0 | pip rows 惰性写一个周期+零行跳判定（§2.4） |
| OS 域数据链断裂（A） | P1 | os 判定随行落表+契约保留 os 段（§2.4） |
| 半更新表无屏草（B） | P1 | last_complete.ts 截断（§2.4） |
| group/dev/constraints 丢失（B） | P1 | 契约保留映射 JSON（§2.4） |
| 新鲜度线缺失（B） | P1 | max(checked_at)>30h ⇒ degraded（§2.4） |
| 写放大 D18（A/B） | P1 | 25 包批 executemany（§2.2） |
| SIGSTOP prod 不可操作（A/B） | P1 | 全链降 dev；prod 自然轮观察（§四.3） |
| judge 双份构造（A-P2） | P2 | audit 侧算好随行落表（§2.5） |
| sec:dep_alert_state 死代码（B-P2） | P2 | 删（§2.6） |
