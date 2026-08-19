# top10_floatholders - 前十大流通股东

> 实测: 2026-08-19 5000积分 | 更新: 季频（随公告） | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（9 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object |  |
| 2 | `ann_date` | object |  |
| 3 | `end_date` | object |  |
| 4 | `holder_name` | object |  |
| 5 | `hold_amount` | object |  |
| 6 | `hold_ratio` | object |  |
| 7 | `hold_float_ratio` | object |  |
| 8 | `hold_change` | object |  |
| 9 | `holder_type` | object |  |

## 数据量
单次返回: 0 行

## 输入参数
```python
top10_floatholders(ts_code='600000.SH', period='20260630')
```
