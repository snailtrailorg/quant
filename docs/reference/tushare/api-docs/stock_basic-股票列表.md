# stock_basic - 股票列表

> 实测: 2026-08-19 5000积分 | 更新: 变更驱动 | 输入模式: full
> [官方文档](https://tushare.pro/document/2)

## 列结构（10 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 000001.SZ |
| 2 | `symbol` | object | 000001 |
| 3 | `name` | object | 平安银行 |
| 4 | `area` | object | 深圳 |
| 5 | `industry` | object | 银行 |
| 6 | `cnspell` | object | PAYH |
| 7 | `market` | object | 主板 |
| 8 | `list_date` | object | 19910403 |
| 9 | `act_name` | object | 无实际控制人 |
| 10 | `act_ent_type` | object | 无 |

## 数据量
单次返回: 5547 行

## 输入参数
```python
stock_basic(list_status='L')
```
