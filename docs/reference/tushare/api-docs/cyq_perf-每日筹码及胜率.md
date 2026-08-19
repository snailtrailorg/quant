# cyq_perf - 每日筹码及胜率

> 实测: 2026-08-19 5000积分 | 更新: 盘后 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（11 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 000001.SZ |
| 2 | `trade_date` | object | 20260818 |
| 3 | `his_low` | float64 | 0.2 |
| 4 | `his_high` | float64 | 20.8 |
| 5 | `cost_5pct` | float64 | 10.0 |
| 6 | `cost_15pct` | float64 | 10.4 |
| 7 | `cost_50pct` | float64 | 10.8 |
| 8 | `cost_85pct` | float64 | 11.4 |
| 9 | `cost_95pct` | float64 | 12.0 |
| 10 | `weight_avg` | float64 | 11.01 |
| 11 | `winner_rate` | float64 | 68.45 |

## 数据量
单次返回: 5540 行

## 输入参数
```python
cyq_perf(trade_date='20260818')
```
