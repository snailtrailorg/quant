# 批 134 · [quality] 告警语义修正 + 全量重建聚合（v2）

> 立项 2026-10-21。**v2（盲审 B 打回后重写）**：P0×2 + P1×4 全处置。v1 把靶心打偏了——真噪声源是**断点检查**（不是 vol=0/跳空）。

## 问题（v2 实测定性 · 17.6M 行真库）

`sync_all_symbols`（全市场全量重建）逐标的调 `_log_bar_quality` → `validate_bar_quality`，每标的 issue 逐条 warning ⇒ 单轮约 34 万条（9880 标的 × 34.5），实测 16 万行/小时挤爆 journald。

**噪声构成（top300 标的实测）**：

| 检查项 | 命中率 | 结论 |
|---|---|---|
| **时序断点（>7 自然日）** | **300/300 标的**，10,347 处（34.5/标的），**96% 落在春节/国庆长假**（间隔 8-11 自然日，交易日连续） | **主噪声源**——df 级启发式把法定长假当断点 |
| 异常跳空 >20% | 179/300（60%）；全库 7,327 命中中**主板几乎全是除权伪命中**（pre_close 未复权，2,162 命中里仅 19 非除权）、920 北交所（±30%）6,062、基金/转债 484（制度本就无 20% 板） | 误报为主，**删** |
| 成交量 0 | **0/300（sync_all 路径）**；全库 3,547 行全在 2026 年 = **仅按日全市场批拉路径**（Tushare daily 返回停牌股） | 对全量重建零贡献 |
| 重复 K 线 / 价 0 | 0/300 | 真检查，保留 |

## 产出（v2）

1. **删时序断点检查**（P0-1，方案 b）：df 级启发式与 `engine._find_gaps`（trade_cal 日历感知 + sync_gap 告警面）语义重复且更差——只有日历面判得了「停牌 vs 缺数据」。删后 34.5 条/标的 → 0。**连带**：`tests/test_batch71_quality.py::test_real_gap_within_symbol_detected`（8 个月=真断点）断言须删/改判据。
2. **删异常跳空检查**（P1-3）：实测命中以除权伪命中（pre_close 未复权）+ 北交所 30% 板 + 基金/转债为主；板别阈值需 板别+ST+新股首日+复牌+除权 五类元数据，df 只有 ohlc/vol/pre_close ⇒ 结构不可行。真脏价已被价 0 检查 + 覆盖校验拦。
3. **停牌语义精确化 + 残差桶**（P1-1）：
   - 停牌谓词：`vol==0 且 open==high==low==close`（须与前收一致可加 pre_close 校验）⇒ 不记 issue（正常）。
   - **残差桶**：`vol==0 但价不齐平` ⇒ 仍记 issue（真异常）——`test_dirty_data_issues` 的 (15,15,15,16,0) 行依赖此桶，删则既有测试红。
4. **检查顺序修正**（P1-2）：停牌掩码**先算**（价 0 检查之前），价 0 检查排除停牌掩码行——防「全 0 OHLC 的停牌行先喷 4 条价 0 并丢行」埋雷（joinquant 等 provider 共用同一 validate）。
5. **结构化 counts**（P1-4）：`validate` 增 additive 键 `counts = {dup, zero_price, suspended_residual, ...}`；`valid` 由 counts 判（不再靠中文字符串匹配——改文案即静默翻转）；聚合直接 sum counts。issues 文案形状不变（兼容承诺）。
6. **聚合**（P0-2）：`sync_all_symbols` 收集 counts → 结束单条 `[quality]` 摘要（覆盖标的数/按类型计数/top5 标的/校验异常标的数）；保留 `[quality]` 前缀（观察期 grep 面依赖）。
   - **落点**：收集器放 `tushare_adapter` 内（validate 同层，engine/base 共用，避免第二份复制）——若实测洪流来自 base.py 按日路径，同一收集器直接可用。

## 限定范围
改：`tushare_adapter.py`（validate 语义 + counts + 收集器）、`engine.py`（sync_all 聚合调用）、`tests/test_batch71_quality.py`（两条断言改判据）、新增聚合用例。**不改** `base.py` 代码——但它三处调用（479/496/504）共享 validate，语义改动自动继承，须跑其测试。

## 验收标准
1. 语义单测（5 类 fixture：正常/停牌齐平/一字板 vol>0/价 0/重复）+ 长假样例（2 月 8 自然日）**不报断点** + 真断 ≥30 天**不报**（断点检查已删，此例转为「不产 issue」断言）
2. **残差桶钉**：vol=0 且价不齐平 ⇒ 必有 issue（test_batch71 的 (15,15,15,16,0) 行）
3. 聚合单测：mock 1000 标的 ⇒ caplog 中 `[quality]` 记录**恰好 1 条**，含 total/类型分布/top；fixture **必须带 pre_close 列**（否则 fail-soft 假绿）
4. 顺序钉：构造全 0 OHLC 停牌行 ⇒ 不产「价 0」issue（走停牌桶）
5. 全量 pytest 绿（含 base.py 三调用点既有测试）

## mock 方式
真 df fixture（含 pre_close）；聚合 mock `_pull_daily_df`/`_save_bars`/`_list_static_ts_codes` + caplog 断言。
