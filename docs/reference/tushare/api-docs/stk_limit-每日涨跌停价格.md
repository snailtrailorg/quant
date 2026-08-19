# stk_limit - 每日涨跌停价格

> 实测: 2026-08-19 5000积分 | 更新: 每个交易日8:40左右 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（4 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `ts_code` | object | 000001.SZ |
| 3 | `up_limit` | float64 | 12.21 |
| 4 | `down_limit` | float64 | 9.99 |

## 数据量
单次返回: 7746 行

## 输入参数
```python
stk_limit(trade_date='20260818')
```
