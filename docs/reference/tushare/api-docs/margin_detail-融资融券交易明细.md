# margin_detail - 融资融券交易明细

> 实测: 2026-08-19 5000积分 | 更新: T+1（交易所次日出） | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（10 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `ts_code` | object | 920992.BJ |
| 3 | `rzye` | float64 | 8159554.0 |
| 4 | `rqye` | float64 | 0.0 |
| 5 | `rzmre` | float64 | 586237.0 |
| 6 | `rqyl` | float64 | 0.0 |
| 7 | `rzche` | float64 | 996177.0 |
| 8 | `rqchl` | float64 | 0.0 |
| 9 | `rqmcl` | float64 | 0.0 |
| 10 | `rzrqye` | float64 | 8159554.0 |

## 数据量
单次返回: 4431 行

## 输入参数
```python
margin_detail(trade_date='20260818')
```
