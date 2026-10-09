# 批 83b · 多源 provider 真路由（阶段二）

> 立项 2026-10-09（主会话补决策后立项；决策 A）。来源＝批 83 阶段二 + 原批 74。
> 关联：批 83a（拆表，已上产）/ `flow/decisions.md` 2026-10-09 决策 A

## 目标

`sync_config.provider` 从「摆设」激活为真路由：数据表选同步来源＝注册的、有对应能力的 data_source 插件集合。

## 现状（已实证）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | 现状「仅 K 线真路由」，其余同步项不读 provider | `flow/任务/批83-数据源交易账号拆分.md` §阶段二 |
| 2 | 四个硬编码 beat 未收编：static-list-sync / convertible-terms / pool-data / full-calibrate | 同上 |
| 3 | 新增源须三处各注册一类，否则串源（确定性 bug） | `data_source.py:169` / `adapters/base.py:95` / `interfaces/base.py:21` |
| 4 | 真路由在 engine.py（handler 分派），不动 `routing.py:resolve()` | 批 83b 盲审 P1-3 |
| 5 | 无第二真数据源（joinquant/ricequant 是 stub 空能力集） | 批 83b 盲审 P1-5/P2 |

## 依赖（就绪）

批 83a 拆表 ✅（已上产）。**立项前置已由决策 A 补**：注入 spy stub 第二源做行为级验证。

## 引用（设计 / 方案）

- `flow/任务/批83-数据源交易账号拆分.md` §阶段二（内容五点）+ §83b 盲审结论（5 P1 + P2）—— 只给指针不复述。

## 产出

1. provider 真路由：engine.py handler 分派读 `cfg["provider"]`（tier1/静态/日历 handler 从硬编码 tushare 改读 provider）。
2. 模块注册制落地：新增源＝三注册表各注册一类（砍「TushareSyncModule」新词）。
3. 四条硬编码 beat 收编到注册制（pool-data 是独立子系统，范围须界定＝仅进注册表可路由 vs fetch 契约重写）。
4. `get_data_source` 对「adapter 已注册但 DataSource 未注册」fail-fast（EX_CONFIG），不静默回落 tushare。
5. 步 0 键集补全：sync_config 3 键无 cfg → 补行（枚举到字面 + schedule/trade_day_filter/enabled 种子值）。
6. 注入 spy stub 第二源（实现 fetch 契约、返回可辨识假数据）供行为级验证。

## 限定范围

只改 `engine.py` handler 分派 + 三注册表 + 四条 beat + `sync_config` 补行 + 测试。
**不碰**：`routing.py:resolve()`（M2 试点）；`delete_by_sync_item` 范围（83b 只对 bar 族提供切换，非 bar 项 provider 恒 tushare）；`sync_kind_config`（kind 维正交）。

## 接口契约

- 调用现有：`get_data_source`（`data_source.py:169`）——新增 fail-fast 分支。
- 不改：`resolve()`（`routing.py`）签名。

## 验收标准

- `cd server && ./venv/bin/python -m pytest tests/ -q` → 全绿（含新增 spy 源用例）
- 行为级：切 provider → 引擎走 spy 而非 tushare（断言数据源对象）；adapter 注册但 DataSource 未注册 → fail-fast EX_CONFIG 不回落
- 反证钉：撤掉 fail-fast → 用例变红（验的是「不静默回落」路径）

## mock 方式

spy stub 源（`provider="spy83b"`）实现 fetch 契约返回假数据；patch `_get_rate_ds`/`get_data_source` 返回 spy 实例；不连真 Tushare。
