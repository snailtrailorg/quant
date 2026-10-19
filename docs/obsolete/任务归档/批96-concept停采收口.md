# 批 96 · concept_sync 停采收口（TIER1_SYNC_IDS 移除 + 监控口径同步）

> 立项 2026-10-09（主会话补决策后立项）。来源＝待办 §一 row 96（2026-10-07 盘查更正，裁定 A 已定）。
> 关联：批 90 P1-A（定案停采）/ 迁移 `0129` / `flow/decisions.md` 2026-10-07

## 目标

concept_sync 上游接口已失效，裁定 A（零消费者 ⇒ 消悬而未决态）：从 `TIER1_SYNC_IDS` 移除 + 同步 `health_monitor` 口径，收口 `0129` 过渡双态哨兵。

## 现状（已实证）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | tushare 概念股分类接口服务端失效（`Error 1054 Unknown column 'name'`，连历史日期亦然），doc_id=125 已 404 | `flow/待办.md` row 96 |
| 2 | 迁移 `0129` 已 `enabled=false`，本地 `sync_log` 曾 1873 次全红 | `server/migrations/versions/0129_concept_sync_disabled.py` |
| 3 | prod worker journal 近 7 天 concept 命中 0（该源不归 worker），与已停采一致 | prod journal 实测 |
| 4 | `concept_sync` 仍在 `TIER1_SYNC_IDS`（一档 9 表） | `server/src/data_platform/tier_tables.py:12-16` |
| 5 | `health_monitor.collector` 遍历 `TIER1_SYNC_IDS` 采集指标 | `server/src/health_monitor/collector.py:329` |
| 6 | `tasks.py` 有 TRANSITION-0129 分支引用 | `server/src/scheduler/tasks.py:450,463` |
| 7 | 另 4 处硬编码 concept_sync 引用（移除后须逐一核去向） | `engine.py:3192`（kind 映射）、`markets.py:99`（ref_data 归置）、`adapters/base.py:244`（能力列表）、`rehearse.py:66` |
| 8 | `health_monitor.collector` 具体遍历在 `:321-331`（非仅 `:329`） | `server/src/health_monitor/collector.py:321-331` |
| 9 | 单测硬编码 `TIER1_SYNC_IDS` 长度/成员 | `test_tier_freshness.py:22/135/157/158` + 5 测 mock 序列 |

## 依赖（就绪）

`tier_tables.py` 单一真源 ✅（tasks.py 与 health_monitor 都只 import 它，改一处即全局）｜无上游依赖。

## 引用（设计 / 方案）

- `flow/方案/数据同步验证方案-20261003.md` P1-A —— concept_sync 停采裁定。
- `flow/decisions.md` 2026-10-07（裁定 A：零消费者 ⇒ 消悬而未决态）。

## 产出

1. `server/src/data_platform/tier_tables.py`：从 `TIER1_SYNC_IDS` 移除 `"concept_sync"`（9 → 8 表）。
2. `server/src/scheduler/tasks.py`：TRANSITION-0129 分支**只删 concept 专属判断、保留 `enabled` 通用 guard**（`enabled` 过滤是通用停采机制，删则未来停采项误报）。
3. 逐一核 4 处硬编码引用去向（`engine.py:3192`/`markets.py:99`/`adapters/base.py:244`/`rehearse.py:66`）：移除后各自应是「死代码可清」或「显式标注 disabled」，不得留「引用不存在 id 的活遍历」。
4. `server/src/health_monitor/collector.py:321-331`：确认遍历 `TIER1_SYNC_IDS` 自动收敛（无需改，但须在验收里证明）。
5. 单测同步：`test_tier_freshness.py` 4 处硬编码 + mock 序列重排；`TIER1_SYNC_IDS` 长度断言 9 → 8。

## 限定范围

只改 `tier_tables.py` + `tasks.py` 的 concept_sync 引用 + 相关单测。
**不碰**：`0129` 迁移本身（保留 enabled=false 现状）、`dc_index`/`dc_member` 重建落表（那是选项②，本次不做，裁定 A 已否）。

## 接口契约

- 改：`TIER1_SYNC_IDS: list[str]` 9 元素 → 8 元素（移除 `"concept_sync"`）。

## 验收标准

- `cd server && ./venv/bin/python -m pytest tests/ -q` → 全绿（含 `test_tier_freshness.py` 改后的 4 处硬编码 + mock 序列）
- `./venv/bin/python -c "from src.data_platform.tier_tables import TIER1_SYNC_IDS; assert 'concept_sync' not in TIER1_SYNC_IDS; print(len(TIER1_SYNC_IDS))"` → `8`
- grep 全仓 `concept_sync`：无残留「活遍历」（死代码已清或显式标注 disabled）；`tasks.py` 仍保留 `enabled` 通用 guard

## mock 方式

纯代码/列表改动，无需 mock；单测即断言列表内容。
