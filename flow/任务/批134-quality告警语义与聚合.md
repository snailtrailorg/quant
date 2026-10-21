# 批 134 · [quality] 告警语义修正 + 全量重建聚合

> 立项 2026-10-21（用户裁定：告警洪流同意立项）。实核发现：洪流大头是**误报**（把正常数据当 issue），语义修正是根、聚合是辅。

## 问题（实核 2026-10-21）

`sync_all_symbols` 逐标的调 `_log_bar_quality(df)`，每标的 0~6 条 issue ⇒ 全市场 1000+ 标的逐条 `logger.warning` ⇒ 全量重建一轮数千条（实测 16 万行/小时挤爆 journald）。

**误报面（大头）**：
- 成交量为 0 ⇒ **停牌是正常数据**（代码备注已自知「停牌可能正常」却仍计 issue）
- 异常跳空 >20% ⇒ 20% 涨跌停板（创业板/科创板）跳空满 20% 是制度常态

每标的的历史几乎必然含停牌与 20% 板 ⇒ 全市场重建时几乎每标的都产 issue ⇒ 洪流。

## 产出（v1）

1. **语义修正**（tushare_adapter.validate_bar_quality）：
   - 停牌（vol=0 且 OHLC 齐平且 high==low）从 issues 移除（正常数据）；**价量为 0（OHLC 有 0）仍保留**（真异常）。
   - 跳空检查移除或按板别阈值（主板 10%/创业科创 20%）——**默认移除**（涨跌停合法数据，误报源）。
2. **全量重建聚合**（engine sync_all_symbols 调用侧）：`_log_bar_quality` 在 sync_all 场景收集而非逐条 warning——结束输出一条摘要（"quality: 812/1000 标的、2345 条 issue，top: …"）。per-symbol 三调用点维持逐条（量小）。
3. **不动**：validate_bar_quality 的返回契约（issues 列表形状——消费面 _log_bar_quality 与观察期查询零改动语义）。

## 限定范围
改：`tushare_adapter.py`（validate_bar_quality 语义）、`engine.py`（sync_all 聚合）。不碰：_log_bar_quality 签名、per-symbol 路径、观察期查询面。

## 验收标准
1. 语义单测：停牌行不产 issue；跳空 20% 板不产 issue；重复/价量为 0 仍产
2. 全量聚合单测：mock 1000 标的 ⇒ 单条摘要 warning（非 3000 条）
3. 全量 pytest 绿

## mock 方式
df fixture 直调 validate_bar_quality；聚合 mock _log_bar_quality 收集。
