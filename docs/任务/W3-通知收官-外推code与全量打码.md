# W3 通知收官批:外推带 code + runbook 后端单源 + 全量打码(2026-09-01 拉前;完美系统战役第三批)

> 排期:plan.md 战役表(原 09-04,推进今日)。收官=通知体系 code 全链(站内已有/外推补齐/映射单源/全站打码)。

## 产出 1:runbook 映射后端单源(#6 前置)

- **新建** `server/src/alert_notify/runbook.py`:`RUNBOOK: dict[str, dict]`(code→{label,guide})——**从 web/src/utils/runbook.js 迁入 10 条+本批新增 ~15 条**(产出 3 的码表);漂移防线:双源→单源,前端改 API 消费
- **新端点** `GET /api/runbook`(system.py;**require_role 四角色枚举**——auth.py:102 平铺无层级,单写 viewer=仅 viewer 可达,admin 反 403,盲审 A-P1)返回 RUNBOOK;**RUNBOOK 暂仅中文**(多语言债声明,盲审 B)
- **前端** MainLayout:**懒挂 loadNotifs 首载**(bellVisible 门内——viewer 无铃不白请求,盲审 A-P2);fetch 失败(如切换窗口 404)下次 loadNotifs 懒补一次;`runbookOf` 改查缓存(签名不变模板零改);utils/runbook.js 删

## 产出 2:外推通道带 code(#6)

- `notify.py`:`notify(...)` 内 `_push_channel(level, title, body, code)` 签名扩展;`report()` 不动(订阅型无 code)
- **终签名** `_push_channel(level, title, body, code=None, channel=None)`(盲审 B-P1:第 4 参现为 channel,字面改会炸 report())——组装在**配额检查后**(免白拼),**先截后拼按终长截**:discord content 上限 2000 字符/企微 4096 字节,原 body 截 1900 再拼处置行,处置行永不落截断区(盲审 A/B-P1:超限发送失败被 channel.py:38 吞=告警静默丢违 D-F1);**顺手修既有隐患**:外推现传原始 body 未截(notify.py:102)
- alert_notify 无邮件通道(wechat_work/discord/serverchan 三通道,channel.py:76;email_service 是邀请邮件独立发件箱不收告警——盲审 A/B 勘误,原"邮件同体"行删)

## 产出 3:全量打码扫荡(#7)

**wrapper 签名扩展四处**(code 透传):
- `strategy_runner/trading.py _alert(title, body, code=None)`
- `strategy_framework/runtime/alerts.py make_alert(..., code=None)` **逐点传**(盲审 A-P1:硬编码吞语义——hub_worker.py:177/186 冻结/拦截应映射旧键 frozen.intercept/buy.blocked,257 盲视→buy.blind;mdlink 4 点逐定)
- `health_monitor/monitor.py _notify(severity, title, body, code=None)`(调用点 3 处:schema 漂移→health.schema-drift/schema 禁用→health.schema-off/恢复→health.recovery;L58 通用组件告警→health.component)
- `scheduler/tasks.py` 直调点剩余(已 6 码,余 ~6:逐点 grep 现场定)

**直调点**(code 定表):
| 文件 | 码 |
|---|---|
| task_manager 任务失败 | task.failed |
| trading.py 207 熔断沿 | risk.halt-edge |
| trading.py 281 对账在场委托 | reconcile.open-orders |
| trading.py 301 WAL 残留 | reconcile.wal |
| runtime/mdlink 4 点 | 现场定(mdlink 族) |
| md_hub make_alert 3 点 | 现场定 |
| scheduler L548/659/1424/1436/1445 | data.disconn/disk.warning/unit.config-err/hub.maint/l3.skip-valkey |
| tushare_adapter 复权降级 | data.adj-degrade |
| llm_gateway/budget 预算预警 | llm.budget |
| data_sync/engine 同步状态 | sync.status |
| alert_failed OnFailure | unit.failed |
| email_service 邮件最终失败 | email.failed |
| strategy_runner/main 余点 | 现场定(预计 0-1) |

**一致性测试**:直调字面量 code ⊆ RUNBOOK 键;**盲区声明**(注释):wrapper 变量透传链(main.py _alert 剥 code/make_alert 产物注入)测不到,靠人工对照
**白名单**(无码=设计):report() 订阅型 info/L105 盘后报告

## 验收
1. pytest 全绿(+wrapper 透传测试/一致性测试/外推 body 追加行测试)
2. 本地:notify(warn, code="l3.failed") 落库 code 正确 + mock 通道收到含"▸ 处置"body
3. 前端:通知页 chip/guide 渲染不回归(API 源);smoke 20/20
4. `grep -rn 'notify(' src/ | grep -v code=` 复查——剩余无码点=白名单明示(如 report/info 类)

## 风险
- wrapper 签名扩展开销面:4 文件调用方全查(grep 漏改=code 静默丢,一致性测试兜)
- 通道 body 追加行对长 body 截断(notify body[:2000] 在站内;外推组装时同截)
- 前端 runbook.js 删除后若缓存 fetch 失败:runbookOf 返回 null→chip 静默不渲染(降级可接受,同现行为)
