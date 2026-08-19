# bak_basic - 每日股本（盘前）

> 实测: 2026-08-19 5000积分 | 更新: 每日8:30左右 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（24 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `ts_code` | object | 000001.SZ |
| 3 | `name` | object | 平安银行 |
| 4 | `industry` | object | 银行 |
| 5 | `area` | object | 深圳 |
| 6 | `pe` | float64 | 4.17 |
| 7 | `float_share` | float64 | 194.06 |
| 8 | `total_share` | float64 | 194.06 |
| 9 | `total_assets` | float64 | 60287.85 |
| 10 | `liquid_assets` | float64 | 0.0 |
| 11 | `fixed_assets` | float64 | 104.64 |
| 12 | `reserved` | float64 | 804.28 |
| 13 | `reserved_pershare` | float64 | 4.14 |
| 14 | `eps` | float64 | 1.24 |
| 15 | `bvps` | float64 | 24.13 |
| 16 | `pb` | float64 | 0.46 |
| 17 | `list_date` | object | 19910403 |
| 18 | `undp` | float64 | 2878.57 |
| 19 | `per_undp` | float64 | 14.83 |
| 20 | `rev_yoy` | float64 | 1.78 |
| 21 | `profit_yoy` | float64 | 3.32 |
| 22 | `gpr` | float64 | 43.66 |
| 23 | `npr` | float64 | 36.39 |
| 24 | `holder_num` | int64 | 450712 |

## 数据量
单次返回: 5554 行

## 输入参数
```python
bak_basic(trade_date='20260818')
```
