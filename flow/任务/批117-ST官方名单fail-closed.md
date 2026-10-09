# 批 117 · ST 官方名单 fail-closed

> 立项 2026-10-09（主会话补决策后立项；决策 D）。**2026-10-09 C 组步 2 双盲审 P0-3/P0-4 返工**：两个核心前提（tushare ST 接口存在性 / namechange offset 支持性）仓内零证据——补**步 0 接口探查**前置；补齐锚点。
> 关联：`flow/decisions.md` 2026-09-24 D1 B-P0 ④ + 2026-10-09 决策 D

## 目标

ST 判定从「`namechange` 派生（戴帽滞后）+ 无档=非 ST（fail-open）」改为**接源侧官方 ST 名单 + 无档 fail-closed**，对齐 `perms.py` 读库失败即拒的不变量。

## 现状（已实证 · 2026-10-09 锚点补齐）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | ST 判定链：`perms.py:139-147` —— `board=="main"` 时经 `SMClient().effective_attr(symbol,"st",today)` 查 `st` 属性；**注释自认**「无档=非 ST（namechange 派生源戴帽滞后 fail-open）；读库失败仍 fail-closed 拒」⇒ **fail-open 与 fail-closed 是两个已区分的态，本批要改的是「无档」这一态** | `server/src/data_platform/perms.py:139-147` |
| 2 | `st` 属性真源＝namechange 派生：`engine.py:3072-3099`（`_make_full_rebuild_handler` 对 `table=="namechange"` 重建后追加派生 `security_state(st)` 时变行，PIT） | `server/src/data_sync/engine.py:3072-3099` |
| 3 | `pull_namechange` 单次调用、无分页无计数校验 ⇒ 结构上无法检测 10000 上限截断 | `tushare_adapter.py:614-622` |
| 4 | namechange 拉取分派点：`(static_list, namechange) → "pull_namechange"`；**全量重建联动**在 engine（重建 namechange 表 ⇒ 派生 st）——分页修复须同时核这两处（否则只改 adapter 不改重建语义＝半修） | `base.py:556` + `engine.py:3099` |
| 5 | tushare「官方 ST 标记接口」**仓内零封装**（grep 全 adapter 无 ST 专用接口）；存在性未验 | 步 0 探查 |

## 依赖（就绪）

namechange 现判定路径 ✅（替换目标）。**步 0 接口探查为本批强制前置**（见产出 0）。

## 引用（设计 / 方案）

- `flow/decisions.md` 2026-09-24 D1 B-P0 ④ + 2026-10-09 决策 D —— 只给指针不复述。

## 产出

0. **步 0 · 接口探查（强制前置，一次核实两个前提）**：
   a. tushare 官方 ST 标记接口存在性（候选：`namechange` 的 change_reason 正则之外，tushare 是否有 `stk_st`/`namechange` 之外的 ST 专用端点；官方文档 doc_id 检索 + dev 实调一次探针）。**结果三分支**：①存在且可用 ⇒ 产 1 按「官方接口」做；②不存在 ⇒ 降级为「namechange 派生强化」（分页修复 + 无档 fail-closed，官方名单从交易所名单人工导入另议）；③不可达/无法判定 ⇒ 本批只做 namechange 分页修复 + fail-closed，官方名单挂账。
   b. `pro.namechange()` offset/limit 分页支持性（dev 实调：`pro.namechange(limit=100, offset=9000)` 是否返回第 9001 条起——若不支持分页则改为「按 ts_code 首字母/交易所分段拉取」或「计数校验告警」）。
1. 接源侧官方 ST 名单（按步 0 结果定形态）。
2. ST 判定读官方名单；**无档改 fail-closed**（`perms.py:139-147` 的「无档=非 ST」分支改为拒绝/告警，与既有「读库失败 fail-closed」同向）。
3. `pull_namechange` 分页/截断修复（`tushare_adapter.py:614-622` + 分派点 `base.py:556` + 重建联动 `engine.py:3099` 三处同核）。

## 限定范围

只改 ST 判定源 + 官方名单接入 + namechange 分页（含重建联动）。
**不碰**：权限三维正交其它面（board/account_permission）、强赎/退市整理期（价格事件）。

## 接口契约

- 改：`perms.py` ST 无档分支（fail-open → fail-closed）。
- 改：`pull_namechange`（按步 0-b 结果定签名：+`offset`/`limit` 参数，或分段策略）。
- 不改：`effective_attr` 接口签名。

## 验收标准

- 步 0 探查结论落任务文件（两前提各一段：结论 + 证据）
- `cd server && ./venv/bin/python -m pytest tests/ -q` → 全绿
- 行为级：无档 → fail-closed（拒绝）；有档 → 正确判定；`pull_namechange` >10000 条不截断（反证钉：单次调用 mock 只回 10000 ⇒ 分页用例红）
- namechange 重建后 st 派生行数不回归（往返验证）

## mock 方式

ST 名单接口 mock；namechange 分页用假数据分段返回验证循环；`perms` 用例 mock `SMClient().effective_attr` 返回 None（无档）/有档两态；不连真 Tushare。
