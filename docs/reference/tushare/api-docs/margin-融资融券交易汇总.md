# margin - 融资融券交易汇总

> 实测: 2026-08-19 5000积分 | 更新: T+1（交易所次日出） | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（9 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `exchange_id` | object | BSE |
| 3 | `rzye` | float64 | 8353062371.0 |
| 4 | `rzmre` | float64 | 747229308.0 |
| 5 | `rzche` | float64 | 771176780.0 |
| 6 | `rqye` | float64 | 62320.0 |
| 7 | `rqmcl` | float64 | 15100.0 |
| 8 | `rzrqye` | float64 | 8353124691.0 |
| 9 | `rqyl` | float64 | 16200.0 |

## 数据量
单次返回: 3 行

## 输入参数
```python
margin(trade_date='20260818')
```
