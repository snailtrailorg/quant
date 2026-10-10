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

namechange 现判定路径 ✅（替换目标）。**步 0 接口探查已完成（2026-10-09 深夜，见下）**。

## 步 0 探查结论（2026-10-09 dev 实调，token 从 .env）

**a. tushare 官方 ST 接口：存在 → 走「官方接口」分支（产 1 形态定了）**
- 接口名＝**`pro.stock_st(trade_date=YYYYMMDD)`**（非 `stk_st`——该名报「请指定正确的接口名」）。
- 实测 2026-10-09 全市场：**201 行**（非整数截断 ⇒ 全量），列 `ts_code/name/trade_date/type/type_name`，`type_name` 分布＝风险警示板 201。
- 支持 `limit/offset` 参数（offset=500 返回 0 行 ⇒ 分页参数被接受；全量 201 < 500 上限 ⇒ 无截断风险）。
- ⚠ pro client 是 `__getattr__` 动态代理：`hasattr` 恒 True，**存在性只能实调判**（本探查法）。

**b. namechange 分页：`limit/offset` 真生效**
- 全量（19000101~20261009）＝**恰好 10000 行**（坐实单次上限截断）。
- `limit=500, offset=0` 与 `offset=500`：各 500 行、首 ts_code 不同（920157.BJ / 920160.BJ）、两页前 10 无交集 ⇒ **分页真翻页**。
- 修法定案：`pull_namechange` 加 `limit/offset` 循环翻页（累积至返回 < limit 即止），消 10000 截断。

**产 1 形态（据 a/b 定）**：新增 `pull_stock_st(trade_date)`（全市场快照，~201 行单页足够，仍带分页参数留扩展）；ST 判定源切 `stock_st` 快照（每交易日同步落表），namechange 降级为历史参考（分页修复保留——与 ST 判定同源弱化收口）。

**步 4 代码双盲审返工补充（2026-10-10，同判件 `步4代码双盲审-C组115-117-同判综合.md`）**：
1. **P0-1 修**：perms 读侧 `vt_to_ts(symbol)` 归一（`schema.py:32` 现成对偶；`account_allows` 收 vt 形态、`st_list.ts_code` 是 tushare 形态，漏转＝表非空后全市场 main 板全拒）＋ params 实值断言反证钉（`test_query_param_is_ts_form_not_vt`，撤转换实测红）。
2. **冷启动窗口语义（P1-1）**：表空/表缺期 ST 判定＝恒非 ST 放行（fail-open 例外）——**窗口有界**：首个 18:10 调度同步成功即闭合；同步持续失败 ⇒ sync_log error + 告警既有机制暴露（不新造监控），窗口延长由告警通道可见。
3. **派生真源归属（P1-2）**：`security_state(st)` 双写源收口——**st_list 派生为主（官方名单，每交易日新鲜）**；namechange 派生（`_derive_st_states`）**降级为历史回填源**（仅补充 st_list 快照起点之前的历史段）。effective_attr 现无读者 ⇒ 冲突面无消费者，归属裁定即收口；若 effective_attr 未来有读者，effective_from DESC 排序天然让新快照行胜出。

**P0-2 语义返工（2026-10-10 prod 首部署实战回滚定案，`8bf98c6`；架构律见 `decisions.md` 同日条）**：
- 原判据「表非空但标的无行 ⇒ fail-closed 拒（两态全集假设）」**结构性错误**：①实测 `stock_st` 是 **ST-only 单态名单**（~200 只 `*ST` 股，5000+ 正常股不在档是正常态——步 0 探了存在性/形状没探名单语义，须抽反例验证）；②**用户点破的生命周期律**：新数据同步模块都要先运行起来才有数据，权限闸门判据不得依赖数据管道启动时序。
- 终态语义：**在档=ST（按 account is_st_allowed 判拒）；不在档=非 ST 放行；表空/表缺=冷启动放行；读库失败仍 fail-closed（不变量）**。判定层只消费正向事实，缺失证据=源未断言≠数据说否；fail-closed 方向归基础设施不归数据内容；数据完整性归同步层自己的 sync_log/gap 体系。
- 时间线存档：11:13:43 beat 重启即触发首同步（新项 `last_sync_ts=NULL` ⇒ 立即补跑，非 18:10——预判失误）；11:14:20 live-task@4 78/CONFIG（600000.SHSE 浦发被拒）；dwell 判死自动回滚，11:17 旧码恢复——**管道安全网首次实战成功**。

## 引用（设计 / 方案）

- `flow/decisions.md` 2026-09-24 D1 B-P0 ④ + 2026-10-09 决策 D —— 只给指针不复述。

## 产出

0. ~~**步 0 · 接口探查（强制前置）**~~ ✅ **已完成（2026-10-09 深夜，结论见「步 0 探查结论」节）**——两个前提均走最优分支：`pro.stock_st()` 官方接口存在（201 行全量）；namechange `limit/offset` 分页真生效。
1. 新增 `pull_stock_st(trade_date)`（`tushare_adapter.py`）：全市场 ST 快照（~201 行，单页足够；签名带 `limit/offset` 留扩展）＋ 新 sync 项（每交易日同步落表，表名 `st_list`；`base.py` 分派 + `sync_config`/`sync_kind_config` 行）。
2. ST 判定读 `st_list` 快照；**无档改 fail-closed**（`perms.py:139-147` 的「无档=非 ST」分支改为拒绝/告警，与既有「读库失败 fail-closed」同向）。
3. `pull_namechange` 分页/截断修复（`tushare_adapter.py:614-622` 加 `limit/offset` 循环翻页，累积至返回 < limit 即止 + 分派点 `base.py:556` + 重建联动 `engine.py:3099` 三处同核）。

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
