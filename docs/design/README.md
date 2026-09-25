# 模块设计文档导航

> 模块级设计与横切规范（原 `docs/architecture/` 的 01-19/21-23/25 号，2026-09-24 拆分）。总体方案与跨模块契约在 `docs/architecture/`，操作手册在 `docs/manual/`，过时归档在 `docs/obsolete/`。

## 清单

| 编号 | 设计文档 | 说明 |
|---|---|---|
| D01 | llm-gateway | AI 调用唯一入口（LLM 路由/工具/用量） |
| D02 | strategy-framework | 三市场统一 Strategy/Factor/Adapter + runtime 骨架 |
| D03 | astock-analysis | A股分析引擎（选股/研判，可实盘） |
| D04 | convertible-etf-engine | 可转债/ETF T+0 引擎 |
| D05 | crypto-perp-engine | 加密永续合约引擎 |
| D06 | data-platform | 数据中台（统一口径 + 限流治理） |
| D07 | risk-control | 双层风控（三级开关/熔断/敞口） |
| D08 | web-admin | Web 管理后台 |
| D09 | scheduler | 定时任务调度（含 SA4 期望表） |
| D10 | alert-notify | 告警/通知（订阅分发三通道） |
| D11 | feishu-lark | 飞书/Lark 对接 |
| D12 | 实盘稳定性设计 | 灾难定义/韧性分层 L1-L3/风险清单 |
| ~~D13~~ | 已归档（被 D26 取代）| 原 hub 需求（ST7） |
| D14 | 共享行情hub设计 | hub 设计 v2（MD 单活双实例/租约 fencing） |
| D15 | 服务监控设计 | health_monitor 内层 + Zabbix 外层 |
| D16 | 多频率数据设计 | 慢路径日线直读/快路径分钟/复权因子链 |
| D17 | 三档数据与详情页 | 选股/深度数据供给 |
| D18 | 数据库操作规范 | 全仓 DB 写路径立法（横切规范） |
| D19 | IM统一接入设计 | IMBotProvider 抽象（横切） |
| D20 | 分钟数据源设计 | 腾讯攒/Tushare 终极/bar_hub 缩小 |
| D21 | 回测报告PTrade全家桶 | 回测绩效口径（α/β/信息率）+ 导出 |
| D22 | 历史数据方案 | 历史数据域完整方案 |
| D23 | 限速抽象聚合 | 限速策略化（RateLimitPolicy） |
| D24 | web设计系统 | 设计令牌体系（tokens.css 规范来源） |

## 任务规格去向（2026-09-25 规则变更）

未完成任务执行规格已迁 **`flow/任务/`**（批号命名，被 `flow/待办.md` 索引引用；本目录回归纯知识 D 系列）；完成后归档 `docs/obsolete/任务归档/` 不变。

## 状态注记约定

每份设计文档头部 blockquote 记录「原编号 + 重大变更注记」；状态真源在 `flow/待办.md`。设计文档描述「为什么与是什么」，代码签名以 `docs/architecture/模块契约/` 为准。
