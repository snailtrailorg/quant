"""三档数据新表清单（项 18，单一真相源）。

盲审遗留收敛（2026-08-22）：tasks.py（告警检测）与 health_monitor/collector.py
（指标采集）各自硬编码同一份清单，改一处漏一处。两层都只依赖 data_platform（合法下行边），
清单归此。

- TIER1_SYNC_IDS：一档 9 表，sync_log 按同步 id 记 success 行
- TIER2_INCREMENTAL_TABLES：二档增量表，pool_data_cursor 有游标（盲审 A-2：_advance_cursors 只推这 4 张）
- TIER2_STATIC_TABLES：二档全量表，无游标无 per-table sync_log（由 pool_data 任务 done 心跳覆盖）
"""

TIER1_SYNC_IDS = [
    "stk_limit_sync", "moneyflow_sync", "margin_detail_sync",
    "top_list_sync", "block_trade_sync", "cyq_perf_sync",
    "forecast_sync", "namechange_sync", "concept_sync",
]

TIER2_INCREMENTAL_TABLES = ["income", "balancesheet", "cashflow", "fina_indicator"]

TIER2_STATIC_TABLES = [
    "cyq_chips", "top10_holders", "dividend", "pledge_stat",
    "share_float", "stk_holdernumber",
]

TIER2_ALL_TABLES = TIER2_INCREMENTAL_TABLES + TIER2_STATIC_TABLES
