# moneyflow_hsgt - 沪深港通资金流向

> 实测: 2026-08-19 5000积分 | 更新: 盘后 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（7 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `trade_date` | object | 20260818 |
| 2 | `ggt_ss` | object | 31921.64 |
| 3 | `ggt_sz` | object | 23015.15 |
| 4 | `hgt` | object | 143517.11 |
| 5 | `sgt` | object | 164122.86 |
| 6 | `north_money` | object | 307639.97 |
| 7 | `south_money` | object | 54936.79 |

## 数据量
单次返回: 1 行

## 输入参数
```python
moneyflow_hsgt(trade_date='20260818')
```
