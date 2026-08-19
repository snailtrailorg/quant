# concept - 概念板块

> 实测: 2026-08-19 5000积分 | 更新: 变更驱动 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（2 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 800001.TS |
| 2 | `name` | object | 猪肉概念 |

## 数据量
单次返回: 1346 行

## 输入参数
```python
concept(trade_date='20260818')
```
