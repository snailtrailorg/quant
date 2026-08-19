# block_trade - 大宗交易

> 实测: 2026-08-19 5000积分 | 更新: 盘后17:00 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（7 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 000007.SZ |
| 2 | `trade_date` | object | 20260818 |
| 3 | `price` | float64 | 11.68 |
| 4 | `vol` | float64 | 90.0 |
| 5 | `amount` | float64 | 1051.2 |
| 6 | `buyer` | object | 华鑫证券有限责任公司重庆江北嘴证券营业部 |
| 7 | `seller` | object | 华鑫证券有限责任公司重庆江北嘴证券营业部 |

## 数据量
单次返回: 58 行

## 输入参数
```python
block_trade(trade_date='20260818')
```
