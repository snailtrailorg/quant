# top_list - 龙虎榜每日明细

> 实测: 2026-08-19 5000积分 | 更新: 盘后17:00-18:00 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（15 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `ts_code` | object | 000551.SZ |
| 3 | `name` | object | 创元科技 |
| 4 | `close` | float64 | 14.0 |
| 5 | `pct_change` | float64 | -9.7357 |
| 6 | `turnover_rate` | float64 | 12.61 |
| 7 | `amount` | float64 | 869926694.0 |
| 8 | `l_sell` | float64 | 122485069.96 |
| 9 | `l_buy` | float64 | 76565606.77 |
| 10 | `l_amount` | float64 | 199050676.73 |
| 11 | `net_amount` | float64 | -45919463.19 |
| 12 | `net_rate` | float64 | -5.28 |
| 13 | `amount_rate` | float64 | 22.88 |
| 14 | `float_values` | float64 | 6781067678.0 |
| 15 | `reason` | object | 日跌幅偏离值达到7%的前5只证券 |

## 数据量
单次返回: 82 行

## 输入参数
```python
top_list(trade_date='20260818')
```
