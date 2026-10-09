# 批 116 · crypto 数据层

> 立项 2026-10-09（主会话补决策后立项；决策 B）。来源＝研究稿 §七「crypto 数据层单独立项」。
> 关联：批 99（期一）/ 批 115（期三）/ `flow/decisions.md` 2026-10-09 决策 B

## 目标

crypto 数据层落地：数据源接入 + 采集器 + 回补 + 落库；时间轴＝`continuous`（24/7 无交易日历）；死构件 `data_increment_crypto` 处置（落地或删除二选一）。

## 现状（已实证）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | crypto 无交易日日历、24/7 连续 | 决策 B |
| 2 | `data_increment_crypto` 每 15min 被 beat 唤醒、永远 `skipped`（死构件） | 研究稿 §七风险4 |
| 3 | 窗口下界＝交易所保留窗口（`start_floor=NULL` 语义） | 研究稿 §六期一 |
| 4 | per-symbol 键＝`(symbol)`（非 `(symbol, trade_date)`） | 决策 B |

## 依赖（就绪）

批 99 期一（族层 `supports_backfill`/`start_floor` 列）✅。**立项前置已由决策 B 补**：时间轴契约已定（`continuous`）。

## 引用（设计 / 方案）

- `flow/方案/数据同步配置架构研究-20261005.md` §七（crypto 数据层单独立项 + 形状须现在容纳）—— 只给指针。
- `flow/decisions.md` 2026-10-09 决策 B —— 时间轴契约。

## 产出

1. 数据源接入（交易所 crypto 源，接入时定）+ 采集器 + 回补 + 落库。
2. 时间轴按 `continuous` 接线（复用契约层 `Temporality` 枚举，不新造词汇）。
3. `data_increment_crypto` 死构件处置（落地或删除二选一，不留死构件）。

## 限定范围

只做 crypto 数据层。
**不碰**：`sync()` 游标契约、配置抽象形状（已在批 99 容纳）、限速熔断（批 73 独立）。

## 接口契约

- 新增：crypto 采集器实现 fetch 契约（三注册表各注册一类，复用批 83b 注册制）。
- 时间轴：`Temporality.continuous`（契约层已有枚举）。

## 验收标准

- `cd server && ./venv/bin/python -m pytest tests/ -q` → 全绿
- 行为级：crypto 源连上、回补落库、时间轴 `continuous` 无交易日过滤
- 死构件：`data_increment_crypto` 落地或删除（不得留 `skipped` 空转）

## mock 方式

crypto 源 mock（返回可辨识假数据）；不连真交易所（源接入前标「服务器测前置」）。
