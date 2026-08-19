# moneyflow_mkt_dc - 大盘资金流向DC

> 实测: 2026-08-19 5000积分 | 更新: 盘后 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（15 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `close_sh` | float64 | 3990.3 |
| 3 | `pct_change_sh` | float64 | 0.19 |
| 4 | `close_sz` | float64 | 14622.5 |
| 5 | `pct_change_sz` | float64 | -0.56 |
| 6 | `net_amount` | float64 | -66932903936.0 |
| 7 | `net_amount_rate` | float64 | -2.79 |
| 8 | `buy_elg_amount` | float64 | -32977604608.0 |
| 9 | `buy_elg_amount_rate` | float64 | -1.37 |
| 10 | `buy_lg_amount` | float64 | -33955299328.0 |
| 11 | `buy_lg_amount_rate` | float64 | -1.41 |
| 12 | `buy_md_amount` | float64 | 15821185024.0 |
| 13 | `buy_md_amount_rate` | float64 | 0.66 |
| 14 | `buy_sm_amount` | float64 | 51111718912.0 |
| 15 | `buy_sm_amount_rate` | float64 | 2.13 |

## 数据量
单次返回: 1 行

## 输入参数
```python
moneyflow_mkt_dc(trade_date='20260818')
```
