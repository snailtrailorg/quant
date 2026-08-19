# share_float - 限售股解禁

> 实测: 2026-08-19 5000积分 | 更新: 事件驱动 | 输入模式: batch_date
> [官方文档](https://tushare.pro/document/2)

## 列结构（7 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object | 600072.SH |
| 2 | `ann_date` | object | 20230822 |
| 3 | `float_date` | object | 20260818 |
| 4 | `float_share` | float64 | 30715032.0 |
| 5 | `float_ratio` | float64 | 2.0468 |
| 6 | `holder_name` | object | 中国船舶集团投资有限公司 |
| 7 | `share_type` | object | 定增股份 |

## 数据量
单次返回: 44 行

## 输入参数
```python
share_float(float_date='20260818')
```
