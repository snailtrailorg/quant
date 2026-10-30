# 批 157 · crypto 盘中档时限根治

> 方案（真源，v2 已盲审处置）：`flow/方案/批157-crypto盘中档时限根治.md`。本文件只写实施步骤与契约。

## 实施步骤
1. engine.py：crypto 拉取主循环 per-symbol except 前加 `_CelerySoftTimeLimit` 窄捕获直抛（照抄 :2494 形态）——**最优先**，时限先能开火
2. engine.py：盘中三档（freeze=False 族）拉取外层改区间分段（整月+残日两级，`_SEGMENT_BUDGET=1800s`）；游标=完整扫过段边界（G-S1：failed 照推）；freeze 档（日线）不切分段
3. tasks.py：新注册 `sync_via_celery_crypto` 变体任务（soft=5400/hard=6000，同函数体）；progress 只透 segments_done/total（去 pct）
4. sync.py：progress 判活改读 `sync:lock:{sid}` 锁键（不存在+running ⇒ 报 stale）；trigger 对 crypto 盘中三档派发变体任务；三档入 _SYNC_ASYNC_DISPATCH
5. tests：分段边界/软直抛/锁判活/游标一致（含 freeze 档整窗零漂移、okx 三档回归钉现状）

## 接口契约
- `sync_via_celery_crypto(sync_id, backfill_from=None, track=True)`：新变体，crypto 盘中三档专属
- handler 返回体新增：`segments_done: int, segments_total: int`
- `/api/sync/trigger/{sid}/progress` status 新词：`stale`（锁键过期判定）
- Valkey `sync:type:{sid}`：last_beat 仅展示不判活

## 读写表
读：sync_config/sync_kind_config/security_master；写：bar_{1min,15min,1h}/sync_log/sync_config(游标)/sync_gap

## 验收
见方案 §四（单测五点/dev 仿真/prod：SoftTimeLimitExceeded 早于 hard=时限开火证明）
