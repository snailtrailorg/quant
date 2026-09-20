# 批 56b：ts 表示层 UTC 统一（23/24 号变更兑现——2026-09-20 勘察改版）

> **勘察改版（2026-09-20 用户裁定）**：原版按 29 号 §四"naive→timestamptz 全表重写"预案编写；
> 开工前勘察实证**前提不成立**——28 张 ts 表已全部 timestamptz（多源战役"ts=+08:00 aware"契约
> 落地时即 aware 列，存的就是 UTC 绝对时刻），db.py 已 pin Asia/Shanghai，tz.py 已统一 aware 写入。
> 本批缩水为**代码表示层统一**（零表结构改动/零数据重写/零迁移）。
> UTC 立法本意（用户 2026-09-20 重申）：库里一切时间=UTC 绝对时刻，显示层按时区换算友好显示，
> 扩展全球市场天然健壮。

## 目标
连接与会话 pin UTC；代码时间构造统一 UTC aware（as_utc 替换 as_shanghai）；全仓 naive 构造清零
（唯一生死面——pin 改 UTC 后残留 naive 写库被解释为 UTC=静默错 8h）；去重键 epoch 归一；
三混回归三处用例钉。

## 依赖（就绪）
- 批 56a ✅
- ~~staging 同规模实测 ALTER 计时~~（勘察删除——无 ALTER）
- 前置②（保留）：staging 预演 deploy_trading_windows 非交易日行为（闸只看星期 %u≤5 不读
  trade_cal；假期白天拦/15:05 后放行）——**行为记录在案即过**，闸不改不升级（用户裁定：
  调试期临时防呆，成熟后整体退役，长远靠预案演练与人工掌控）
  **✅ 2026-09-20 预演实证**（staging --check + deploy_fake_now + enforce=true）：
  周四 10:30 盘中=断言红"盘中拒绝发布"（EXIT=2）；周四 20:30=放行（All assertions passed）。
  结论：假期（工作日星期）部署选 15:05 后晚上窗，零改动零 force。注：staging 组默认
  enforce=false（彩排不设限），预演须显式 -e deploy_trading_window_enforce=true。
- 前置③（保留，降险）：节前 10/6-10/7 端到端同步演练+回测冒烟（节后开市前把错 8h 提前暴露）

## 产出
- `tz.py`：as_utc(dt)（naive→UTC aware；已 aware→astimezone(UTC)；签名同 as_shanghai）；
  as_shanghai 保留给显示层换算用（或迁移后全仓唯一消费点=显示工具）
- `db.py`：`-c TimeZone=UTC`（一行+重启；**不改任何存储值**——timestamptz 下会话时区只影响
  显示与 naive literal 解释）
- 全仓 6 处 as_shanghai 调用点改 as_utc（adapters×4/md_hub×1/tencent_minute×1）
- **naive 构造清零**：全仓 datetime.now()/utcnow()/naive 构造盘点→写库路径全部 aware 化
  （读侧 naive 消费同步排查）
- 去重键：worker (symbol,ts) 比较归一 Unix 秒（int(ts.timestamp())——epoch 函数义，29 号歧义消解）
- 回归用例三处（本节定义，29 号 §四预案 4"锁范围不锁用例"）：
  1. **hub 聚合**：构造 09:29/09:31 两根 1min bar（UTC aware）→ 聚合窗口按上海日切不裂根
  2. **暖机拼接**：PG bar_1min 30 天（UTC 读）+流回放 240 根（hub ISO）→ 拼接处无重复无缺口
  3. **日界沿**：15:00 后到根 bar 归属下一数据日（day_anchor 表驱动）→ UTC 表示下沿判定一致
- market_session 表 2 个 naive timestamp 列：活表（quant_common.session 消费）——若是 created_at
  类非 ts 语义列则本批不动（挂账）；若涉时刻语义则顺带 aware 化

## 限定范围
只改时间表示层与 naive 构造——不改任何存储值/业务逻辑/表结构（行为等价：同一时刻，同一存储）。

## 接口契约
as_utc(dt)->datetime(UTC aware)（tz.py）；去重键比较走 int(ts.timestamp())。

## 验收标准
①迁移前后逐表聚合校验和一致（count+sum(epoch(ts))——即使零结构变更也跑，双保险）
②三处回归用例绿 ③现有测试全绿（时区断言同步修）④停服+beat 暂停+同版本原子上线三检查单过
（naive 残留错 8h 风险=同版本原子上线的原因，检查单保留）⑤部署窗=假期/晚上窗（15:05 后闸放行）。

**✅ 2026-09-20 编码收官实证（dev）**：
- ①双会话校验和恒同：`bar_1D` 13,650,836 行 `sum(extract(epoch from ts))` 在 UTC 会话与
  Asia/Shanghai 会话下完全一致——pin 只改显示，物理存储（epoch）零变更铁证；
  同一行双表示对拍 `2026-09-17 16:00+00:00 == 2026-09-18 00:00+08:00` ✓
- ②`tests/test_tz_utc.py` 11 钉（as_utc 纯函数族/epoch 键跨表示同刻同键/水位兼容/hub 聚合 UTC/
  日界沿/读写收口含 mock 绑参断言）
- ③**1251 全绿**（1240 基线+11；时区断言同步修 5 处：hub 分钟末/flush_rest/tencent 0931+1501/
  tushare aware 偏移/validate_bars 收口）
- 行为级：etf_daily 同步真跑 42,365 行写（UTC pin+收口）→get_bars naive 窗口读回 UTC aware ✓
- 改动面：tz.py（as_utc）/db.py（pin+三读函数收口+validate_bars 写收口）/6 调用点 as_utc/
  hub_worker（_epoch_key 键值分离：比较链 epoch/传值链 UTC ISO/Valkey 水位兼容转换）/
  parts.py 流协议 UTC 表示（与 worker 同版本原子）

## 部署 runbook（前置③演练后执行——假期晚上窗）
1. **prod 校验和基线**（部署前）：经 ansible 通道跑
   `SELECT count(*), sum(extract(epoch from ts)) FROM bar_1D`（+bar_1min 等 ts 表）记录存档
2. 停服（web/celery/hub/task 全波次）+ beat 暂停——release.yml 波次重启即满足（迁移与代码同版原子）
3. staging 彩排绿 → prod 发布（晚上窗 15:05 后）
4. **部署后校验和对照**：同查询结果与基线逐表一致=零数据变更
5. 次日同步链观察（etf/astock/cb 同步 pulled/saved 正常+K 线图表 ts 显示正常）
6. 前端显示层已随批修：`fmtTime.day()` 新增+5 文件 slice 断裂点全改（BacktestView×5/Trading×1/
   Dashboard×1/LLMUsageCards×1/UserManagement×1——字符串 slice 在 UTC ISO 下错位 8h/跨日；
   `new Date()` 解析点天然安全未动）；上线后观察 K 线/回测/权益曲线 x 轴日期

## mock 方式
tz 转换单测纯函数；三处回归用例真库（行为级）。

## 参考文档
29 号 §四（工程预案六条——①⑤条随勘察失效，②③④⑥条保留精神）；28 号 §3.2（tz 立法）。
