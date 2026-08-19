# express - 业绩快报

> 实测: 2026-08-19 5000积分 | 更新: 按公告日增量 | 输入模式: batch_ann
> [官方文档](https://tushare.pro/document/2)

## 列结构（15 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 000028.SZ |
| 2 | `ann_date` | object | 20260818 |
| 3 | `end_date` | object | 20260630 |
| 4 | `revenue` | float64 | 36266356000.0 |
| 5 | `operate_profit` | float64 | 834430400.0 |
| 6 | `total_profit` | float64 | 827384900.0 |
| 7 | `n_income` | float64 | 606203600.0 |
| 8 | `total_assets` | float64 | 48170513900.0 |
| 9 | `total_hldr_eqy_exc_min_int` | float64 | 18873323000.0 |
| 10 | `diluted_eps` | float64 | 0.99 |
| 11 | `diluted_roe` | float64 | 3.21 |
| 12 | `yoy_net_profit` | float64 | 665907300.0 |
| 13 | `bps` | float64 | 30.83 |
| 14 | `perf_summary` | object | None |
| 15 | `update_flag` | object | 0 |

## 数据量
单次返回: 1 行

## 输入参数
```python
express(ann_date='20260818')
```
