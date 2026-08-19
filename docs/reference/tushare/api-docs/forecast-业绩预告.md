# forecast - 业绩预告

> 实测: 2026-08-19 5000积分 | 更新: 按公告日增量 | 输入模式: batch_ann
> [官方文档](https://tushare.pro/document/2)

## 列结构（13 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 688208.SH |
| 2 | `ann_date` | object | 20260818 |
| 3 | `end_date` | object | 20260630 |
| 4 | `type` | object | 略减 |
| 5 | `p_change_min` | float64 | -11.54 |
| 6 | `p_change_max` | float64 | -6.34 |
| 7 | `net_profit_min` | float64 | 42500.0 |
| 8 | `net_profit_max` | float64 | 45000.0 |
| 9 | `last_parent_net` | float64 | 48046.62 |
| 10 | `first_ann_date` | object | 20260818 |
| 11 | `summary` | object | 预计2026年1-6月归属于上市公司股东的净利润盈利:42, |
| 12 | `change_reason` | object | 1、报告期内,公司持续以"AI智能化"为核心,围绕行业关键痛 |
| 13 | `update_flag` | object | 0 |

## 数据量
单次返回: 1 行

## 输入参数
```python
forecast(ann_date='20260818')
```
