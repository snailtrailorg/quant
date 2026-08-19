# moneyflow_dc - 个股资金流向DC（东财口径）

> 实测: 2026-08-19 5000积分 | 更新: 盘后 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（15 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `ts_code` | object | 000725.SZ |
| 3 | `name` | object | 京东方Ａ |
| 4 | `pct_change` | float64 | 6.41 |
| 5 | `close` | float64 | 6.47 |
| 6 | `net_amount` | float64 | 88581.4 |
| 7 | `net_amount_rate` | float64 | 4.32 |
| 8 | `buy_elg_amount` | float64 | 117623.83 |
| 9 | `buy_elg_amount_rate` | float64 | 5.74 |
| 10 | `buy_lg_amount` | float64 | -29042.43 |
| 11 | `buy_lg_amount_rate` | float64 | -1.42 |
| 12 | `buy_md_amount` | float64 | -31791.95 |
| 13 | `buy_md_amount_rate` | float64 | -1.55 |
| 14 | `buy_sm_amount` | float64 | -56789.45 |
| 15 | `buy_sm_amount_rate` | float64 | -2.77 |

## 数据量
单次返回: 6000 行

## 输入参数
```python
moneyflow_dc(trade_date='20260818')
```
