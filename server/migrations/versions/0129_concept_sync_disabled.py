"""concept_sync 停采（enabled=false）——上游 tushare concept 接口在服务端不可用（P1-A 定案）。

## 事实链（2026-10-03 数据同步验证第一步 · P1-A）

本地 dev 库 `sync_log`：`concept_sync` 最近一次 success = **2026-09-14**，之后 **1873 次全红**，
错误 `Error 1054 (42S22): Unknown column 'name' in 'field list'`（MySQL 42S22 = 列不存在）。

**隔离实测**（本地真调 TUSHARE_TOKEN，2026-10-03 当日）：
- `pro.concept()`（不带参数） → `必填参数, trade_date`
  ⇒ 上游把「可选」参数改成了**必填**（契约变更）；
- `pro.concept(trade_date='20261002')` → `查询数据失败，请确认参数！可以反馈管理员协助您排查问题, Error 1054 ...`
- `pro.concept(trade_date='20251002')` → **同样 1054**（**过去日期亦然**）

⇒ 连历史日期都 1054，且错误话术是 tushare 平台侧标准文案（「可以反馈管理员协助您排查」）
⇒ **不是本仓用法错**（`pull_concept` 只做 `pro.concept(**kwargs)` 无 fields 参数），
是**该接口在上游服务端已不可用**：老式「概念股分类」接口（登记 `code/name/src`，
官方文档页 doc_id=125 现已 404），上游现役等价能力是 `dc_index`（概念板块，2024/12/20 起，
5000 积分）+ `dc_member`（成分股）。

## 为什么选「停采」而不是「换 dc_index」

1. **换 `dc_index` 不是修 bug，是新产品需求**：`dc_index` 输出 15 个字段（含行情/领涨股/
   涨跌家数/换手率），而本仓 `concept` 表**只有 `ts_code + name` 两列**——schema 装不下，
   必须新建表 + 新 sync_config + 新 handler + 新落库映射。不该挂在「修 P1-A」名下。
2. **停采代价 ≈ 0（零消费者，落码前逐点验过）**：
   - 后端：`concept` 表在全仓唯一引用是 `data_platform/data_source.py:140` 的**限流配置**（0.3s）；
   - 前端：`web/src` 对 concept **零引用**、无组件、无菜单项；
   - 无 API 端点、无因子、无策略消费。
3. **消掉的是残余噪音**：旧代码 + P1-B 未修时它每 300s 触发一次（重试风暴）；批 90（P1-B）
   已把它退避为「每周一 07:00 一次失败 + 一次告警」；本迁移再进一步停止采集，把这一条也消掉。

## TRANSITION-0129（过渡双态哨兵）

`concept_sync` **仍保留在** `data_platform/tier_tables.py::TIER1_SYNC_IDS` 内——维持「一档 9 表」
的**分级语义**（concept 确实属盘后日频族），但 `enabled=false`（停止采集）。这是**过渡态**：
- 断流检测（`scheduler/tasks.py::_check_tier_freshness`）与指标采集（`health_monitor/collector.py`）
  已改为**尊重 `enabled`**——已禁用的 sync 不参与断流告警 / 新鲜度指标（禁用＝平台主动放弃
  该数据，再报「断流」是语义噪音）。
- **收口条件**（二选一）：① 确认放弃该能力 → 从 `TIER1_SYNC_IDS` 移除（本哨兵随之撤）；
  ② 上游恢复或改用 `dc_index` 重新设计落表 → 改回 `enabled=true`（检测自动回归监控）。

## 为什么无往返用例

往返脚本的 scratch fixture **不建 `sync_config` 行**（`fixture_paper` 只建交易域），而 0129 改的
正是 `sync_config` 数据 ⇒ 无 fixture 可依。`UPDATE ... AND enabled` 双条件已保证复跑幂等
（同 0128 纪律：只碰需要改的行，复跑零写放大）。

Revision ID: 0129
Revises: 0128
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0129"
down_revision: Union[str, None] = "0128"
branch_labels: Union[Sequence[Union[str, None]], str] = None
depends_on: Union[Sequence[Union[str, None]], str] = None


def upgrade() -> None:
    # WHERE 双条件幂等：已 false 的行不再碰（复跑零写放大）。
    op.execute("UPDATE sync_config SET enabled = false "
               "WHERE id = 'concept_sync' AND enabled")


def downgrade() -> None:
    # 对称恢复：改回 enabled=true（断流检测/指标采集经 enabled 过滤自动回归监控）。
    op.execute("UPDATE sync_config SET enabled = true "
               "WHERE id = 'concept_sync' AND NOT enabled")
