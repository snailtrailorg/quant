"""回补能力注册表（批170 立，层1：data_sync 向下引用）：`sync_config.supports_backfill` 的**代码真源**。

为什么单独成表：该列历史上靠迁移 0131/101/101b/102b/103b/116/117 逐批 seed，
而批166 的「添加任务」建行路径绕过了 seed ⇒ 新建同步项静默失去回补入口
（batch99 能力真源测试 DB 比对揪出）。此处集中字面量：
  - 迁移 seed 与之保持一致；
  - 建行（data_platform.data_source.create_sync_configs）按本表落值；
  - tests/test_batch99_sync_backfill_capability.py 同时钉「本表 == 期望字面量」与「DB 行 == 本表」。

语义：=该 sync_id 的 handler **真消费** `backfill_from`（能按区间重拉），不是“形态标签”。
"""
from __future__ import annotations

BACKFILLABLE_IDS: frozenset[str] = frozenset({
    # bar 族 6（engine._VIA_KIND_IDS）
    "astock_daily", "listed_fund_daily", "cb_daily", "index_daily",
    "astock_minute", "astock_minute_5min",
    # tier1 批量 8（engine._TIER1_BATCH）
    "stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "top_list_sync",
    "block_trade_sync", "cyq_perf_sync", "forecast_sync", "st_list_sync",
    # 单表专项 12
    "astock_basic", "trade_cal", "binance_perp_daily", "astock_daily_jq",
    "binance_perp_hourly", "binance_perp_1min", "binance_perp_15min",
    "okx_perp_daily", "okx_perp_hourly", "okx_perp_1min", "okx_perp_15min",
})
