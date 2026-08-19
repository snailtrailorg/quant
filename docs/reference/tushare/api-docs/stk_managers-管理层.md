# stk_managers - 管理层

> 实测: 2026-08-19 5000积分 | 更新: 变更驱动 | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（11 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 600000.SH |
| 2 | `ann_date` | object | 20260107 |
| 3 | `name` | object | 龚德雄 |
| 4 | `gender` | object | M |
| 5 | `lev` | object | 董事会成员 |
| 6 | `title` | object | 董事 |
| 7 | `edu` | object | 硕士 |
| 8 | `national` | object | 中国 |
| 9 | `birthday` | object | 19690101 |
| 10 | `begin_date` | object | 20260106 |
| 11 | `end_date` | object | None |

## 数据量
单次返回: 244 行

## 输入参数
```python
stk_managers(ts_code='600000.SH')
```
