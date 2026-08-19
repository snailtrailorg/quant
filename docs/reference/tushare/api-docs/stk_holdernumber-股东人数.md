# stk_holdernumber - 股东人数

> 实测: 2026-08-19 5000积分 | 更新: 季频 | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（4 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 600000.SH |
| 2 | `ann_date` | object | 20260608 |
| 3 | `end_date` | object | 20260608 |
| 4 | `holder_num` | float64 | nan |

## 数据量
单次返回: 128 行

## 输入参数
```python
stk_holdernumber(ts_code='600000.SH')
```
