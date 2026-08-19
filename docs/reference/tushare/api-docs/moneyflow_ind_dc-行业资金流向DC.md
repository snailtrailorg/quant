# moneyflow_ind_dc - 行业资金流向DC

> 实测: 2026-08-19 5000积分 | 更新: 盘后 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（18 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `content_type` | object | 行业 |
| 3 | `ts_code` | object | BK0433.DC |
| 4 | `name` | object | 农林牧渔 |
| 5 | `pct_change` | float64 | 4.81 |
| 6 | `close` | float64 | 14669.19 |
| 7 | `net_amount` | float64 | 3634202176.0 |
| 8 | `net_amount_rate` | float64 | 9.99 |
| 9 | `buy_elg_amount` | float64 | 3133463872.0 |
| 10 | `buy_elg_amount_rate` | float64 | 8.61 |
| 11 | `buy_lg_amount` | float64 | 500738304.0 |
| 12 | `buy_lg_amount_rate` | float64 | 1.38 |
| 13 | `buy_md_amount` | float64 | -927761920.0 |
| 14 | `buy_md_amount_rate` | float64 | -2.55 |
| 15 | `buy_sm_amount` | float64 | -2396955648.0 |
| 16 | `buy_sm_amount_rate` | float64 | -6.59 |
| 17 | `buy_sm_amount_stock` | object | 中粮糖业 |
| 18 | `rank` | int64 | 1 |

## 数据量
单次返回: 1031 行

## 输入参数
```python
moneyflow_ind_dc(trade_date='20260818')
```
