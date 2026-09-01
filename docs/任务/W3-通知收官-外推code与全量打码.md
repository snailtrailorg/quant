# W3 通知收官批:外推带 code + runbook 后端单源 + 全量打码(2026-09-01 拉前;完美系统战役第三批)

> 排期:plan.md 战役表(原 09-04,推进今日)。收官=通知体系 code 全链(站内已有/外推补齐/映射单源/全站打码)。

## 产出 1:runbook 映射后端单源(#6 前置)

- **新建** `server/src/alert_notify/runbook.py`:`RUNBOOK: dict[str, dict]`(code→{label,guide})——**从 web/src/utils/runbook.js 迁入 10 条+本批新增 ~15 条**(产出 3 的码表);漂移防线:双源→单源,前端改 API 消费
- **新端点** `GET /api/runbook`(system.py;require_role viewer)返回 RUNBOOK
- **前端** MainLayout:启动时 fetch 一次 `/api/runbook` 存模块级缓存,`runbookOf` 改查缓存(签名不变,模板零改);utils/runbook.js 删(或留空壳 re-export?——删,W1 刚上但未上产第二次,无兼容包袱)

## 产出 2:外推通道带 code(#6)

- `notify.py`:`notify(...)` 内 `_push_channel(level, title, body, code)` 签名扩展;`report()` 不动(订阅型无 code)
- `channel.py` `send()`:body 尾部追加 `\n▸ 处置: {label}——{guide}`(code 在 RUNBOOK 时);channel send 签名怎么改——**不改 send**(通道实现多元),由 `_push_channel` 组装终 body 后传(通道层零感知)
- 邮件通道同体(text body 追加行;不做 HTML 链接——站内通知页需登录,外链意义小,处置文字已够)

## 产出 3:全量打码扫荡(#7)

**wrapper 签名扩展四处**(code 透传):
- `strategy_runner/trading.py _alert(title, body, code=None)`
- `strategy_framework/runtime/alerts.py make_alert(...)` → code="runtime.guard"(通用守卫)
- `health_monitor/monitor.py _notify(severity, title, body, code=None)`(调用点 3 处:schema 漂移→health.schema-drift/schema 禁用→health.schema-off/恢复→health.recovery;L58 通用组件告警→health.component)
- `scheduler/tasks.py` 直调点剩余(已 6 码,余 ~6:逐点 grep 现场定)

**直调点**(code 定表):
| 文件 | 码 |
|---|---|
| task_manager 任务失败 | task.failed |
| tushare_adapter 复权降级 | data.adj-degrade |
| llm_gateway/budget 预算预警 | llm.budget |
| data_sync/engine 同步状态 | sync.status |
| alert_failed OnFailure | unit.failed |
| email_service 邮件最终失败 | email.failed |
| strategy_runner/main 余点 | 现场定(预计 0-1) |

**一致性测试**(防打码与映射漂移):`grep 全仓 code="..." 字面量 ⊆ RUNBOOK 键`(单测跑 collect+断言——打码必有映射,映射可多不可少)

## 验收
1. pytest 全绿(+wrapper 透传测试/一致性测试/外推 body 追加行测试)
2. 本地:notify(warn, code="l3.failed") 落库 code 正确 + mock 通道收到含"▸ 处置"body
3. 前端:通知页 chip/guide 渲染不回归(API 源);smoke 20/20
4. `grep -rn 'notify(' src/ | grep -v code=` 复查——剩余无码点=白名单明示(如 report/info 类)

## 风险
- wrapper 签名扩展开销面:4 文件调用方全查(grep 漏改=code 静默丢,一致性测试兜)
- 通道 body 追加行对长 body 截断(notify body[:2000] 在站内;外推组装时同截)
- 前端 runbook.js 删除后若缓存 fetch 失败:runbookOf 返回 null→chip 静默不渲染(降级可接受,同现行为)
