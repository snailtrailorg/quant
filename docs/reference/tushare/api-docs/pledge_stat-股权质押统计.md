# pledge_stat - 股权质押统计

> 实测: 2026-08-19 5000积分 | 更新: 不定期 | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（7 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 600000.SH |
| 2 | `end_date` | object | 20260814 |
| 3 | `pledge_count` | int64 | 1 |
| 4 | `unrest_pledge` | float64 | 663.13 |
| 5 | `rest_pledge` | float64 | 0.0 |
| 6 | `total_share` | float64 | 3330583.83 |
| 7 | `pledge_ratio` | float64 | 0.02 |

## 数据量
单次返回: 625 行

## 输入参数
```python
pledge_stat(ts_code='600000.SH')
```
