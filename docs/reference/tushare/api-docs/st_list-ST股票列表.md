# ST股票列表 - 替代方案

> 实测: 2026-08-19 | 无独立 st_list 接口 | 输入模式: stock_basic 过滤

## 替代方案

```python
# 方案 1：stock_basic 过滤名称含 ST
df = pro.stock_basic(list_status='L', fields='ts_code,name')
st_stocks = df[df['name'].str.contains('ST', na=False)]
# 实测 2026-08-19: 206 只

# 方案 2：namechange 跟踪 ST 状态变更（含历史）
df = pro.namechange(ts_code='600000.SH')  # 曾用名/变更原因
```

## 实测数据量
- 当前 ST/*ST 股票: **206 只**（2026-08-19 实测）
- 退市股: 339 只（list_status='D'）

## 用途
选股引擎过滤 ST 股票（避雷）——选股因子跑完后 `EXCLUDE st_list`
