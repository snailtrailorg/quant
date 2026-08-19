# concept_detail - 概念板块成分

> 实测: 2026-08-19 | 接口名需用 `concept` | 输入模式: batch_date

## 调用方式
```python
df = pro.concept(trade_date='20260818')  # 获取当日概念板块列表
# 列: ts_code, name
# 实测 1346 个概念

# 获取某概念的成分股：需要用其他方式（查文档确认）
```

## 列结构
| 列名 | 说明 |
|---|---|
| `ts_code` | 概念板块代码（如 BK0500） |
| `name` | 概念名称 |

## 注意
- `concept_detail` 不是正确的接口名
- 正确接口：`concept(trade_date=...)` 获取列表
- 成分股查询可能需要更高积分或单独接口
