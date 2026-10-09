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

## 依赖（就绪）

`tier_tables.py` 单一真源 ✅（tasks.py 与 health_monitor 都只 import 它，改一处即全局）｜无上游依赖。

## 引用（设计 / 方案）

- `flow/方案/数据同步验证方案-20261003.md` P1-A —— concept_sync 停采裁定。
- `flow/decisions.md` 2026-10-07（裁定 A：零消费者 ⇒ 消悬而未决态）。

## 产出

1. `server/src/data_platform/tier_tables.py`：从 `TIER1_SYNC_IDS` 移除 `"concept_sync"`（9 → 8 表）。
2. `server/src/scheduler/tasks.py`：清理 TRANSITION-0129 相关分支（`:450,463`），确认无残留对 concept_sync 的 TIER1 遍历假设。
3. `server/src/data_platform/adapters/base.py`：确认 `:244` 的能力列表对 concept_sync 的处置（停采后该项应从可拉列表移除或标注 disabled）。
4. 单测同步：`TIER1_SYNC_IDS` 长度断言（若有）从 9 → 8。

## 限定范围

只改 `tier_tables.py` + `tasks.py` 的 concept_sync 引用 + 相关单测。
**不碰**：`0129` 迁移本身（保留 enabled=false 现状）、`dc_index`/`dc_member` 重建落表（那是选项②，本次不做，裁定 A 已否）。

## 接口契约

- 改：`TIER1_SYNC_IDS: list[str]` 9 元素 → 8 元素（移除 `"concept_sync"`）。

## 验收标准

- `cd server && ./venv/bin/python -m pytest tests/ -q` → 全绿
- `./venv/bin/python -c "from src.data_platform.tier_tables import TIER1_SYNC_IDS; assert 'concept_sync' not in TIER1_SYNC_IDS; print(len(TIER1_SYNC_IDS))"` → `8`
- grep 全仓 `concept_sync` 无残留 TIER1 遍历假设

## mock 方式

纯代码/列表改动，无需 mock；单测即断言列表内容。
