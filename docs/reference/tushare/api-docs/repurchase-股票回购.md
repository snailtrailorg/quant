# repurchase - 股票回购

> 实测: 2026-08-19 5000积分 | 更新: 公告驱动 | 输入模式: batch_ann
> [官方文档](https://tushare.pro/document/2)

## 列结构（9 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 600585.SH |
| 2 | `ann_date` | object | 20260818 |
| 3 | `end_date` | object | 20260817 |
| 4 | `proc` | object | 实施 |
| 5 | `exp_date` | object | None |
| 6 | `vol` | float64 | 30989800.0 |
| 7 | `amount` | float64 | 567837257.16 |
| 8 | `high_limit` | float64 | 17.43 |
| 9 | `low_limit` | float64 | 17.32 |

## 数据量
单次返回: 22 行

## 输入参数
```python
repurchase(ann_date='20260818')
```
