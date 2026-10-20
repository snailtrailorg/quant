# 批 131 · pool 数据 symbol 形态（vt ↔ tushare ts_code）· v2

> 立项 2026-10-20（批 95 验收真跑暴露）。**v2**：双审 A 7 P1 / B 2 P0+4 P1 全处置。

## 问题（v2 纠正影响面）

内部形态=vt（600000.SHSE：bar 表 PK / Subscription / pool_symbols / DataBus 全 vt）；源形态=ts_code（600000.SH）。

- 日常轮/全量轮 **不受影响**：_get_pool_ts_codes（pool_data.py:67-72）自 83b 起已 vt_to_ts 归一。v1 写成「全败」= 事实错误。
- 坏路一：POST /api/sync/pool-data/trigger?pool_id=（sync.py:117-121）把 pool_symbols.symbol 原值(vt)直接下发 run_pool_sync(symbols=)。
- 坏路二：DataBus consume（databus.py:270 传 vt）⇒ bar_daily consume / bar_minute **静默型**形态错位（空帧 0 行、无 partial 无告警）。
- 根因：BaseDataAdapter.to_source_symbol（base.py:168）注释写「内部 ts_code → 源格式」——契约假装内部是 ts，实际是 vt；tushare 信注释不覆写 ⇒ 直通 ⇒ 错位。

## 产出（v2）

1. TushareAdapter.to_source_symbol 覆写（base.py）：**委托 schema.to_ts_code**（不新造映射表）+ 后缀白名单。
   - 映射：SHSE/SSE→SH（.SSE 是本仓合法形态：fees.py:33 / risk.py:340 / tasks.py:241 实证）、SZSE→SZ、BSE→BJ。
   - 幂等：.SH/.SZ/.BJ 原样（**日常轮热路径必经分支**——test_pool_specs.py:287 默认传 ts）。
   - 空串返回空串（维持既有空帧语义）。
   - 未知后缀/裸码 ⇒ ValueError（消息带原值+白名单）。.OF（无产生者）/.SZE（trade_cal 交易所名非 vt 后缀）硬拒并写明。
   - 大小写：ex.upper() 后匹配。
2. base.py:168 docstring 重写（本 bug 成因）：入参=内部 vt；已源格式幂等；未知响亮。顺带收敛 joinquant_adapter.py:201 表述。
3. fetch 入口一次性归一：fetch() 顶部 syms = tuple(self.to_source_symbol(s) for s in (req.symbols or ()))，四处 per-symbol 分支（:450 pull_daily / :473 pull_index_daily / :481 pull_minute / :503 pull_pool_table）改用 syms。**不逐个调用点接**（防第五分支漏）。
4. trigger?pool_id= 归一（sync.py:117-121）：symbols=[vt_to_ts(r[0]) for r in rows]（验收必须钉这条路径——add 入池路径本就传 ts，测它=saved>0 假绿）。
5. create_pool 写侧归一（backtest.py:63-73）：复用 add 的 to_vt_symbol + vt_to_ts 校验（必须带合法后缀）⇒ 防混合形态再生（现状裸码/ts 都收，stock_detail in_pool 对 ts 形态行恒判非池）。
6. 手动/定向回补失败告警（pool_data.py log_round）：errors 非空时调 engine._alert_sync_failure——本 bug 潜伏至今正是手动路径零告警。

## 挂账（本批不做）
大池时间盒饿死（280s 无续跑起点）· _repull_symbol 剥裸码传给 fetch_supply · pool 十表不在 109/110 对账 · vt_to_ts/to_ts_code 双名去重 · 存量形态盘点 SQL。

## 限定范围
改：adapters/base.py、web_api/routes/sync.py、web_api/routes/backtest.py、data_sync/pool_data.py、tests/（新增 + test_fetch_contract.py:112/123/146 三处断言 vt→ts 必改）。
不碰：tushare_adapter.py 的 pull_* 签名、schema 映射表、engine。

## 验收标准
1. 形态矩阵：SHSE→SH / SZSE→SZ / BSE→BJ / SSE→SH / SH·SZ·BJ 幂等 / 空串→空串 / 裸码与 .UNKNOWN→ValueError / 小写 .shse→.SH
2. 接线钉（既有范式 test_pool_specs._adapter_with_pro + pro.cyq_chips.call_args）：fetch(池内族, symbols=('600000.SHSE',)) ⇒ ts_code=='600000.SH'；拆掉入口归一即红
3. consume 三条路径用例（静默型失败面）：bar_daily consume / bar_minute / 池内族，均断言下游收到 ts
4. 幂等热路径回归：fetch(池内族, symbols=('600000.SH',)) 成功
5. pool_id 端点路径端到端：mock rows 返 vt ⇒ run_pool_sync 收到 ts
6. create_pool 归一：裸码/ts/vt ⇒ 库里统一 vt
7. 全量 pytest 绿（含 test_fetch_contract 改后）
8. prod 真跑：trigger?pool_id=test-pool ⇒ saved>0 且 cyq_chips 含 20260930
