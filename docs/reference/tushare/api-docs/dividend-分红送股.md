# dividend - 分红送股

> 实测: 2026-08-19 5000积分 | 更新: 公告驱动 | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（14 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 600000.SH |
| 2 | `end_date` | object | 20251231 |
| 3 | `ann_date` | object | 20260331 |
| 4 | `div_proc` | object | 预案 |
| 5 | `stk_div` | float64 | 0.0 |
| 6 | `stk_bo_rate` | float64 | nan |
| 7 | `stk_co_rate` | float64 | nan |
| 8 | `cash_div` | float64 | 0.0 |
| 9 | `cash_div_tax` | float64 | 0.42 |
| 10 | `record_date` | object | None |
| 11 | `ex_date` | object | None |
| 12 | `pay_date` | object | None |
| 13 | `div_listdate` | object | None |
| 14 | `imp_ann_date` | object | None |

## 数据量
单次返回: 76 行

## 输入参数
```python
dividend(ts_code='600000.SH')
```
