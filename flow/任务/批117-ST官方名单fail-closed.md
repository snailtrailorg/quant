# 批 117 · ST 官方名单 fail-closed

> 立项 2026-10-09（主会话补决策后立项；决策 D）。来源＝D1 代码双盲审 B-P0 第④条（`flow/decisions.md` 2026-09-24）。
> 关联：`flow/decisions.md` 2026-10-09 决策 D

## 目标

ST 判定从「`namechange` 派生（戴帽滞后）+ 无档=非 ST（fail-open）」改为**接源侧官方 ST 名单 + 无档 fail-closed**，对齐 `perms.py` 读库失败即拒的不变量。

## 现状（已实证）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | 现 ST 判定走 `namechange` 派生（戴帽滞后）＋无档＝非 ST ⇒ fail-open | `flow/待办.md` row 43 |
| 2 | `perms.py` 读库失败即拒（fail-closed 不变量） | `server/src/…/perms.py`（编码期定位） |
| 3 | 「股票曾用名恰好 10000 条」＝上游单次上限截断，供 ST 识别的 `pull_namechange` 与 ST 判定同源弱化 | `flow/待办.md` §二 row 63 |
| 4 | 无档 fail-open 与权限面 fail-closed 矛盾（同一判定面两种方向） | 决策 D |

## 依赖（就绪）

`namechange` 现判定路径 ✅（替换目标）。**立项前置已由决策 D 补**：修法源＝源侧官方 ST 名单。

## 引用（设计 / 方案）

- `flow/decisions.md` 2026-09-24 D1 B-P0 ④ + 2026-10-09 决策 D —— 只给指针不复述。

## 产出

1. 接源侧官方 ST 名单（优先 tushare 官方 ST 标记接口，其次交易所名单）。
2. ST 判定读官方名单；无档改 fail-closed（拒绝/告警，不静默当非 ST）。
3. 同步 `namechange` 分页截断修复（10000 条上限，`tushare_adapter.py:614-622` limit/offset 循环翻页）——与 ST 判定同源，一并收口。

## 限定范围

只改 ST 判定源 + 官方名单接入 + `namechange` 分页。
**不碰**：权限三维正交的其它面（board/account_permission）、强赎/退市整理期（价格事件）。

## 接口契约

- 改：ST 判定函数读官方名单（签名不变，来源变）。
- 改：`pull_namechange` 加 `limit`/`offset` 循环翻页。

## 验收标准

- `cd server && ./venv/bin/python -m pytest tests/ -q` → 全绿
- 行为级：无档 → fail-closed（拒绝/告警）；有档 → 正确判定 ST
- `namechange` 翻页：>10000 条不截断（反证钉：单次调用无分页 → 用例红）

## mock 方式

官方 ST 名单接口 mock；`namechange` 分页用假数据分段返回验证循环翻页；不连真 Tushare。
