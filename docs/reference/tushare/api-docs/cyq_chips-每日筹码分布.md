# cyq_chips - 每日筹码分布

> 实测: 2026-08-19 5000积分 | 更新: 盘后 | 输入模式: **per_symbol**（按标的查，不支持按日批量）
> [官方文档](https://tushare.pro/document/2?doc_id=286)

## 列结构（4 列，实测）

| # | 列名 | 类型 | 说明 |
|---|---|---|---|
| 1 | `ts_code` | str | TS代码 |
| 2 | `trade_date` | str | 交易日期 |
| 3 | `price` | float | 价格档位 |
| 4 | `percent` | float | 该价位筹码占比（%） |

## 调用示例
```python
df = pro.cyq_chips(ts_code='600000.SH', trade_date='20260818')  # 单标的单日
# 134 行 = 134 个价格档位的筹码分布
```

## 注意
- **per-symbol 接口**：必须传 ts_code，不能按 trade_date 批量全市场
- 每标的一天约 100-150 行（价格档位数量）
- 配合 cyq_perf（汇总统计版，支持批量）使用
