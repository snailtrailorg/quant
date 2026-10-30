# 批 157 · crypto 盘中档时限根治（v3 · 实施后终态）

> 立项 2026-10-30，**同日实施完成**（a79a7bd 步1 / f1da34a 步2-5）+ **步4 代码双盲审处置**（7a081a6：P0 二轮补拉钳 _scanned_hi 防 游标越窗——A/B 同撞定谳；backfill 禁段预算；前端 stale 消费）。审报告归档 `flow/稳定性检查/盲审批157-158-步4代码审-{A,B}报告.md`。
> 关联：批 110（时限显式声明）· 批 144/145（僵尸误报）· 批 101/101b/105/116（crypto bar 链与游标契约）。

## 一、病根（实证三条）

1. **soft 被吞**（B-2/A 同撞，独立核验定谳）：celery 软时限窄捕获只在 `_repull_symbol` 循环（engine.py:2494），crypto 拉取主循环的 per-symbol `except Exception` 会吞 `SoftTimeLimitExceeded`——10-30 两证（97.3%/95.6% 死于 hard 4200 而非 soft 3600）即实证。时限机制对 crypto 档整体是哑的。
2. **SIGKILL 价值毁灭**：hard kill 无 finally，游标不写 ⇒ 从头重拉（upsert 幂等保数据、工时全耗）。
3. **活性真源缺失**：progress 键（TTL 1h）与 last_status 都分不清「在跑/死残留」——立项当日两次误读皆此。

## 二、设计（v2 四件套）

### 2.1 区间分段（不是逐日分段——B-3/A-P2 处置）

拉取循环外层按**日期区间**切段：整月优先、残月按日（`_period_keys` 两级粒度直接复用，月包优化不丢）：

```
segs = 区间切分(窗)（整月片 + 残日片）
for seg in segs:
    if elapsed > _SEGMENT_BUDGET: break      # 段边界查预算（1800s）
    for sym in 标的: 拉(seg.lo, seg.hi)       # 整段一次拉（月包路径 >90 天自动生效）
    cursor_upto = seg.hi                      # 段边界=游标安全点
```

- **游标语义（B-1 处置，G-S1 不动）**：游标=「已完整**扫过**的段边界」——per-symbol 失败照旧进 failed_dates+告警、游标照推。绝不回到「全部成功才推」的 livelock 形态。
- **freeze 档隔离（A-P0 处置）**：`freeze_on_partial=True` 的日线档**不切分段**（整窗行为零漂移）；分段只施于盘中三档（freeze=False 族）。这同时消解 A 证的「freeze 锚 end-11 越窗跳」风险——冻结分支在分段路径上不存在。
- 月包请求量数学（A-P2）：180 天窗=6 月片+残日片 ≈ 7×1004 请求 vs 逐日 180×1004——两级粒度是必要约束非优化。

### 2.2 软时限直抛（本批最优先——时限先要能开火）

crypto 拉取主循环 per-symbol `except` 前加窄捕获：

```python
except Exception as e:
    if _CelerySoftTimeLimit is not None and isinstance(e, _CelerySoftTimeLimit):
        raise                      # 软时限直抛：中止本任务，已 commit 段不丢，游标=最后完整段
    ...原 per-symbol 失败记录...
```

（照抄 2494 先例的形态；直抛后 sync() except 落 error 行+游标按段边界写——soft 从此真正开火，hard 只兜 socket 挂死。）

### 2.3 活性真源=SyncLock 锁 TTL（B-6/A-P1 处置：不另立第三真源）

- **判活读 `sync:lock:{sid}` 键存在性**（20s 心跳线程续期、60s TTL 天然过期）——与源速完全解耦，拥塞段 ~6 标的/h 也不误报。
- progress 端点：running 且锁键不存在 ⇒ 报 `stale`（新词）。`last_beat` 字段保留但只作进度展示，不做判活。
- 零新活性机制：lock TTL 是唯一真源（既有），progress 键只是展示面。

### 2.4 时限声明（A-P1/B-7 处置：任务注册级，变体任务承接）

- celery 时限是任务注册级不可按消息分档 ⇒ 新注册 `sync_via_celery_crypto` 变体任务（同函数体，装饰器 soft=5400/hard=6000；= 2.6 段预算+SM/收尾余量，数字自洽可审计）承接 crypto 盘中三档；`sync_via_celery` 本体维持 3600/4200 不动。
- trigger 路由：sid ∈ crypto 盘中三档白名单 ⇒ 派发变体任务（sync.py 一处 if）。
- **调度路径（B-4 处置）**：盘中三档加进 `_SYNC_ASYNC_DISPATCH`——为将来 cron 化铺路且防误配置内联饿死调度轮；当下维持 manual-only（存储闸门裁定不翻案，cron 化=用户显式决策另批）。

### 2.5 回补接力语义（A-P1/B-5 处置：选 b 变体——增量路径接力）

- 盘中档游标从 NULL 到追平的全过程走**增量路径**（非 backfill）：游标未闭合时增量窗口=retention 地板~publish 上界，每任务连拉至段预算尽，游标按段推进，**下一轮增量自动从新游标续**——「周而复始」由既有增量机制承担，不改 backfill 契约（`_advance=None` 不动）。
- 手动全窗回补单次触发=拉 2-3 段即正常返回（success、部分完成）；用户连续触发或等 cron 化后自动追平。验收措辞按此改（不再宣称「一次触发自动多轮」）。

## 三、限定范围

改：engine.py（分段外层+窄捕获+游标段边界）、tasks.py（`sync_via_celery_crypto` 变体+progress 展示字段）、sync.py（progress stale 判定+变体路由+dispatch 白名单）、tests 四套（分段边界/软直抛/锁判活/游标一致）。
不碰：backfill 游标契约、freeze 档路径、okx 档行为（回归钉现状：101b 游标/105 冻结/尾区 skipped 三套断言）、SyncLock 本体、G-S1。

## 四、验收

1. 单测：①段预算触界⇒终止+游标=最后完整段+failed 非空时游标仍推（G-S1）②软时限异常直抛（mock _CelerySoftTimeLimit）③锁键不存在+status=running⇒stale ④月片切分含残月 ⑤freeze 档整窗零漂移。
2. dev 仿真：mock 源限速跑 3 段窗，断言两任务接力追平+游标中间态落库。
3. prod（上产后）：手动触发 binance_perp_hourly——journal 出现 SoftTimeLimitExceeded **早于** hard（时限开火证明）；连续触发至 success；okx 三档回归绿。

## 实施终态（2026-10-30，v3）

- **步1（a79a7bd）**：soft 窄捕获直抛——首轮+二轮补拉两处（engine.py :1390/:1410 区），3 新测（test_batch157_softlimit_reraise）。
- **步2-5（f1da34a）**：区间分段（_segment_window 整月+残日 / _SEGMENT_BUDGET=1800s / 游标=reached 段边界接力 / freeze 档整窗不切 / progress 只发首段）；sync_via_celery_crypto 变体（5400/6000，函数体抽 _sync_via_celery_body）；_CRYPTO_INTRADAY_SIDS 单源（web trigger + scheduler dispatch 共用）六档入 _SYNC_ASYNC_DISPATCH（expires=5400）；progress 端点锁判活（running+锁键不存在 ⇒ stale + stale_reason）。守门测试（dispatch 键集 / progress per-symbol）随批更新。
- 上产验证点：手动触发 binance_perp_hourly 全窗——journal 出现 SoftTimeLimitExceeded **早于** hard；连续触发至 success。

## 盲审处置对照（v2 历史）

| 发现 | 级 | 处置 |
|---|---|---|
| B-1 游标全成功才推=livelock | P0 | 采纳：改「完整扫过」语义（§2.1） |
| A freeze 锚 end-11 越窗跳 | P0 | 采纳：freeze 档不切分段（§2.1）+验收⑤ |
| A/B soft 被吞（独立同撞） | P0 | 采纳：窄捕获直抛（§2.2，最优先） |
| A/B 回补接力无机制支撑 | P1 | 采纳 a 变体：增量路径接力，验收措辞改（§2.5） |
| A/B 时限不可消息级分档 | P1 | 采纳：变体任务 sync_via_celery_crypto（§2.4） |
| A/B 心跳 120s 拥塞误报 | P1 | 采纳：锁 TTL 判活，last_beat 仅展示（§2.3） |
| B-3/A-P2 逐日丢月包 ×15 请求 | P1/P2 | 采纳：整月+残日两级粒度（§2.1） |
| B-4 dispatch 未覆盖 | P1 | 采纳：入 _SYNC_ASYNC_DISPATCH（§2.4） |
| A pct 每片重置乱跳 | P2 | 采纳：只透 segments_done/total，去 pct（§2.1） |
| B-7 时限数字互相矛盾 | P2 | 采纳：5400/6000 重算自洽（§2.4） |
