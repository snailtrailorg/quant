# 批 134 · [quality] 告警语义修正 + 全量重建聚合（v3）

> 立项 2026-10-21。v1 靶心打偏（B 审 17.6M 行实测打回）→ v2 重写 → **v3（复审 5 项 P1 处置，无剩余 P0）**。

## 问题（实测定性 · 17.6M 行真库）

`sync_all_symbols` 逐标的调 `_log_bar_quality` → `validate_bar_quality`，每标的 issue 逐条 warning ⇒ 单轮约 34 万条（9880 × 34.5），实测 16 万行/小时挤爆 journald。

| 检查项 | 实测 | 结论 |
|---|---|---|
| **时序断点（>7 自然日）** | **300/300 标的中标**，10,347 处（34.5/标的），**96% 落在春节/国庆长假**（间隔 8-11 自然日，交易日连续） | **主噪声源**，删 |
| 异常跳空 >20% | 179/300；全库 7,327 命中以**除权伪命中**（pre_close 未复权，主板 2,162 里仅 19 非除权）+ 北交所 920（±30%）6,062 + 基金/转债 484 为主 | 误报为主，删 |
| 成交量 0 | **0/300（sync_all 路径）**；全库 3,547 行全在 2026 年 = 仅按日全市场批拉路径（Tushare daily 返回停牌股） | 对本批零贡献 |
| 重复 K 线 / 价 0 | 0/300 | 真检查，保留 |

## 产出（v3）

1. **删时序断点检查**：df 级启发式与 `engine._find_gaps`（trade_cal 日历感知 + sync_gap 落表）重复且更差——只有日历面判得了「停牌 vs 缺数据」。连带：`test_real_gap_within_symbol_detected`（8 个月）断言删除；`test_per_symbol_gap_not_cross_symbol` 变恒真 ⇒ **删除**。
2. **删异常跳空检查**：板别阈值需 板别+ST+新股首日+复牌+除权 五类元数据，df 只有 ohlc/vol/pre_close ⇒ 结构不可行；真脏价已被价 0 + 覆盖校验拦。
3. **停牌语义 + 残差桶**：
   - 停牌谓词：`vol==0 且 open==high==low==close` ⇒ 归 `suspended` 桶，**不产 issue 文案**。
   - 残差桶：`vol==0 但价不齐平` ⇒ 归 `suspended_residual`，**产 issue**（`test_dirty_data_issues` 的 (15,15,15,16,0) 行依赖此桶）。
4. **检查顺序**（P1-2 更正事实）：停牌掩码**先算**，价 0 检查**排除停牌掩码行**。真实后果不是「丢行」（`clean` 被所有调用方丢弃，无数据损失），而是**误报**：全 0 OHLC 的停牌行会先命中 open 列的价 0 检查喷一条 issue（且该行随即被 drop，停牌桶永远看不到它）。修后停牌行走停牌桶，不产价 0 issue。
5. **结构化 counts**（键全集，不留省略号）：
   ```python
   counts = {"dup": int, "zero_price": int, "suspended": int, "suspended_residual": int}
   ```
   - 空 df / 无异常分支**同样返回四键全 0**（累加侧无 KeyError）。
   - `valid` 映射**保持原语义**（原实现用中文串过滤：含「为 0」「停牌」的 issue 不计入 invalid）⇒ `valid = counts["dup"] == 0`。这是刻意保留（不是遗漏），实现处写注释说明，防未来误改。
   - `issues` 仍为 `list[str]`（形状不变；全仓仅 engine/base 读 `.get("issues")`），counts 为 additive 键。
6. **聚合（零全局态）**：**counts 随 validate 返回值携带**，`sync_all_symbols` 循环内**局部累加**，结束由 `data_platform` 下**纯函数**渲染摘要字符串，`engine` 打一条 `[quality]` warning。
   - 不在 adapter 内建收集器/全局态（复审①：全局态会跨轮残留、无 reset 即无界）。
   - **sync_all 路径不再逐 issue warning**，只出这一条摘要（否则「恰好 1 条」做不到）。
   - 摘要字段：覆盖标的数 / 四类计数 / top5 标的（按 issue 数）/ 校验器异常标的数。
7. **docstring 同车重写**：`tushare_adapter.validate_bar_quality` 与 `tests/test_batch71_quality.py` 模块 docstring 现写「去重、价量为 0、异常跳空、时序断点」——改为实际四项（重复/价 0/停牌/残差）。

## 限定范围

改：`tushare_adapter.py`（语义 + counts + docstring）、`engine.py`（sync_all 局部累加 + 单条摘要）、`data_platform` 新增摘要纯函数（可放 `adapters/quality.py`）、`tests/test_batch71_quality.py`（删两条断点断言、改判据）、新增聚合用例。
**不改** `base.py` 代码——其三处调用（479/496/504）共享 validate，语义自动继承，须跑其测试。

## 验收标准

1. 语义单测（5 类 fixture：正常 / 停牌齐平 / 一字板 vol>0 / 价 0 / 重复）：停牌齐平不产 issue；一字板不产 issue；价 0 与重复仍产。
2. **残差桶钉**：vol=0 且价不齐平 ⇒ 必产 issue。
3. **聚合单测**：mock 1000 标的 ⇒ caplog 中 `[quality]` 记录**恰好 1 条**（不含校验器异常行——异常行走既有 fail-soft 通道），含 total/四类计数/top；fixture 必须带 `pre_close` 列。
4. **顺序钉**：构造全 0 OHLC 停牌行 ⇒ 不产「价 0」issue（走停牌桶）。
5. 全量 pytest 绿（含 base.py 三调用点既有测试）。
6. **生产量化口径**（上产后首个全量重建）：单轮 `[quality]` 行数 = 1（修复前 16 万行/小时）。

## mock 方式

真 df fixture（含 pre_close）；聚合 mock `_pull_daily_df`/`_save_bars`/`_list_static_ts_codes` + caplog。
