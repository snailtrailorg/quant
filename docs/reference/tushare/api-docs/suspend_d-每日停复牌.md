# suspend_d - 每日停复牌

> 实测: 2026-08-19 5000积分 | 更新: 盘前 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（4 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 300176.SZ |
| 2 | `trade_date` | object | 20260818 |
| 3 | `suspend_timing` | object | None |
| 4 | `suspend_type` | object | S |

## 数据量
单次返回: 6 行

## 输入参数
```python
suspend_d(trade_date='20260818')
```
