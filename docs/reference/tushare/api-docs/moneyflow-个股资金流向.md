# moneyflow - 个股资金流向

> 实测: 2026-08-19 5000积分 | 更新: 盘后完整（盘中有部分） | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（20 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 300937.SZ |
| 2 | `trade_date` | object | 20260818 |
| 3 | `buy_sm_vol` | int64 | 34502 |
| 4 | `buy_sm_amount` | float64 | 10321.63 |
| 5 | `sell_sm_vol` | int64 | 32786 |
| 6 | `sell_sm_amount` | float64 | 9819.31 |
| 7 | `buy_md_vol` | int64 | 36175 |
| 8 | `buy_md_amount` | float64 | 10827.29 |
| 9 | `sell_md_vol` | int64 | 36458 |
| 10 | `sell_md_amount` | float64 | 10914.29 |
| 11 | `buy_lg_vol` | int64 | 17399 |
| 12 | `buy_lg_amount` | float64 | 5208.72 |
| 13 | `sell_lg_vol` | int64 | 19913 |
| 14 | `sell_lg_amount` | float64 | 5957.01 |
| 15 | `buy_elg_vol` | int64 | 5320 |
| 16 | `buy_elg_amount` | float64 | 1599.19 |
| 17 | `sell_elg_vol` | int64 | 4239 |
| 18 | `sell_elg_amount` | float64 | 1266.22 |
| 19 | `net_mf_vol` | int64 | -5960 |
| 20 | `net_mf_amount` | float64 | -1762.13 |

## 数据量
单次返回: 5540 行

## 输入参数
```python
moneyflow(trade_date='20260818')
```
