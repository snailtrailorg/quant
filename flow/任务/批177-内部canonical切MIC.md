# 批 177 · 内部 canonical 切 ISO 10383 MIC（全量）

> 2026-11-14 用户裁定：**全量切 MIC**；crypto 每个市场给自定义后缀。
> 本篇覆盖并**推翻**批175 的「canonical 保持 vt」口径（那条是我单方建议，被误记为"用户裁定"——已在 decisions 更正）。

## 一、目标形态

| 市场 | 内部 canonical | 依据 |
|---|---|---|
| 上交所 | `600036.XSHG` | ISO 10383 MIC |
| 深交所 | `000001.XSHE` | ISO 10383 MIC |
| 北交所 | `830799.BJSE` | ISO 10383 MIC |
| 币安永续 | `BTCUSDT.XBIN` | **自定义**（官方表无此码，已核 ISO CSV 未占用） |
| OKX 永续 | `BTCUSDT.XOKX` | **自定义**（同上） |

- 入站别名表仍接受 `SHSE/SZSE/BSE/SSE/SH/SZ/BJ/XSHG/XSHE/BJSE` 与旧 crypto 写法（`BINANCE/OKX`），
  统一 `canonical()` 成上表。
- 证券主键仍是「代码.MIC」（非 ISIN）；`security_master.mic_code` 退化为与后缀同值（可后续删列）。

## 二、波及面（实测）

- **代码**：`src` 91 行 / 33 文件（含注释；逻辑点约 40）；`web/src` 30 行；`tests` **567 行 / 90 文件**（多为夹具字面量，机械替换 + 少量语义修正）。
- **表**（行数=dev）：`bar_1d 17,875,021`、`security_state 519,116`、`screen_factor_cache 441,396`、`daily_basic.vt_symbol 438,524`、`risk_log 22,018`、`bar_1h 131,553`、`bar_15min 8,640`、`bar_1min 7,712`、`bar_index 5,245`、`broker_usage 4,134`、`sync_gap 4,290`、`security_master 10,813`、`strategy_config 5`、`freeze_event 4`、`backtest_symbols 2`、`pool_symbols 2`、`order_log 1`、`reconcile_issue`；另有 `security_master.exchange` 列值域、`trading_account.params` / `account_permission.allowed_exchanges` 里的交易所名。
- **行为面**：hub 流键/订阅键、vnpy 枚举转换、ts_code 双向映射、风控市场分项、EMT market 映射、回测/选股/对账、前端下拉与显示。

## 三、迁移策略（0185，单次原子）

后缀等长替换优先（`.SHSE→.XSHG`、`.SZSE→.XSHE` 等长；`.BSE→.BJSE`、`.BINANCE→.XBIN`、`.OKX→.XOKX` 变长）：
```sql
replace(replace(replace(replace(replace(symbol,
  '.SHSE','.XSHG'), '.SZSE','.XSHE'), '.BSE','.BJSE'), '.BINANCE','.XBIN'), '.OKX','.XOKX')
```
按表执行；`bar_*` 走逐表重写（PK 含 symbol → 全表重写 + 索引重建，`bar_1d` 为最大项）。
**须在维护窗**：先 `pg_dump` 全量备份 → 迁移 → 校验（每表 `count(*)` 不变、0 行旧后缀）→ 上产代码（同一次 release）。

`daily_basic.vt_symbol`、`screen_factor_cache.vt_symbol` 为派生列，同批替换；`reconcile_issue.symbol` 里的
`account#N` 不受影响（替换条件按后缀匹配）。

## 四、分步（本次执行）

1. **S1 单一映射层**：`symbology` canonical=MIC + crypto 自定义码 + 反向别名（旧 vt 仍可入）。
2. **S2 代码扫尾**：`schema`/`transport`/`translate`/`md_gateway`/`markets`/`fees`/`risk`/`perms`/`adapters`/`data_sync`/`screen`/`routes` 全部改按 MIC。
3. **S3 迁移 0185** + dev 演练（downgrade→upgrade→断言）。
4. **S4 夹具/测试机械替换 + 语义修正**（567 行），全量绿。
5. **S5 前端**（30 行：`EX` 表与示例文案 → MIC）。
6. **S6 上产（维护窗）**：备份 → release（迁移+代码同车）→ 校验：各表 0 行旧后缀、hub 订阅键、持仓/委托/选股/对账端点、下单链。

## 五、回滚

- 代码：整版本 rollback（`rollback-tasks.yml`）。
- 数据：迁移 `downgrade` 反向替换（可逆：后缀一一对应），**但须与代码回滚同批**，否则新旧口径互不识别。
- 备份：上产前 `pg_dump` 全量（`scripts` 现有备份任务同路径），保留至验证通过。

## 六、验收

- 全量 pytest 绿（含 `test_symbology` 新表：`canonical('600036.SHSE')=='600036.XSHG'` 等）。
- dev 真库：0185 往返；17 表 0 行 `\.(SHSE|SZSE|BSE|BINANCE|OKX)$`。
- prod：`/api/position`/`/api/orders`/`/api/screen/*`/`/api/reconcile/issues` 全 MIC；hub 流键带 MIC；下一交易日开市首笔成交/持仓写回正常。
