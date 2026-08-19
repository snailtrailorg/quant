# pledge_detail - 股权质押明细

> 实测: 2026-08-19 5000积分 | 更新: 不定期 | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（14 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 600000.SH |
| 2 | `ann_date` | object | 20140324 |
| 3 | `holder_name` | object | 雅戈尔集团股份有限公司 |
| 4 | `pledge_amount` | float64 | 1200.0 |
| 5 | `start_date` | object | 20130730 |
| 6 | `end_date` | object | 20140227 |
| 7 | `is_release` | object | 1 |
| 8 | `release_date` | object | 20140324 |
| 9 | `pledgor` | object | 厦门国际信托有限公司 |
| 10 | `holding_amount` | float64 | nan |
| 11 | `pledged_amount` | float64 | nan |
| 12 | `p_total_ratio` | float64 | 0.04 |
| 13 | `h_total_ratio` | float64 | nan |
| 14 | `is_buyback` | object | 0 |

## 数据量
单次返回: 2 行

## 输入参数
```python
pledge_detail(ts_code='600000.SH')
```
