# web 长尾第一档:冒烟门 + 通知 code 字段 + DSL 因子后端(2026-09-01 立项,15号复审遗留第一档)

> 来源:flow/待办.md web backlog(B5)+P2 队列。三项独立交付,一批走八步法。

## 产出 1:B5 冒烟门(scripts/smoke-web.sh)

- **文件**:`scripts/smoke-web.sh`(新)
- **内容**:登录(admin/admin123 本地 dev 缺省,`SMOKE_USER/SMOKE_PASS` 环境可覆写)→ 带 token 逐端点 curl → 断言 **HTTP 状态 + JSON 顶层形状**(只打状态/结构,不打数据值——避免数据漂移误红)
- **端点集**(GET 为主,~15 个):auth/me/strategy/factors/live-task/notifications/dashboard 概要/risk 水位/reconcile/screener 三端点/pool/**/api/position**(非 positions,trading.py:200)/backtest 列表/health/**help/index**(system.py:24,topic 必带)。逐条 `ok/FAIL` 输出+末尾计数,非零退出
- **自限**(盲审 B-P1):登录 **401 即中止不重试**(login 限流 10/min/IP auth_routes.py:43;prod admin 密码随机 auth.py:319,连试只会烧限流);全脚本单轮不循环
- **范式**:`scripts/verify.sh`(set -euo pipefail+中文进度输出);登录 `POST /api/auth/login`(auth_routes.py:58,返回 token)→ `Authorization: Bearer`
- **接线**:`flow/规范/八步法.md` 步 5 提及(web 交付前跑);`scripts/LOCAL-DEPLOY.md` 一行
- **不做的**:release postverify 集成(=P1-2 管道级,另批);POST 写路径冒烟(只读)

## 产出 2:通知 code 字段(结构化 body 机制)

- **文件**:`migrations/versions/0059_*.py`(新)+`server/src/alert_notify/notify.py`+`server/src/web_api/routes/system.py`(notifications GET **在 system.py:300 非 mgmt.py**,SELECT :314)+`web/src/layouts/MainLayout.vue`+`web/src/utils/runbook.js`(新)
- **迁移**:`ALTER TABLE notifications ADD COLUMN code varchar(64)`(NULL 兼容存量)
- **notify()**:`notify(level, category, title, body, source_ref, code=None)`+INSERT 带 code;`safe_notify` 同步透传(它没有 category——补 category="system" 缺省?**不**:safe_notify 签名加 code 即可,category 保持现行为)
- **GET /api/notifications**:SELECT 加 code 列返回
- **前端**:通知列表项加 code chip;`runbook.js` code→{label,处置一句话}映射表(首版 ~10 条,见下);未映射 code 只显 chip 不报错
- **首批打码 call sites**(starter,渐进补;盲审 A/B-P1:md_hub 全文件零 notify、gen_jump 消费侧告警是 13号明令不做——**md_hub 项撤销**,只打真锚):
  - `scheduler/tasks.py`:l3.failed(:1465)/sa4.restart(:1444 区)/reconcile.error(对账异常处)
  - `strategy_runner/main.py`:deps.exhausted(:288);`_gated_send`:frozen.intercept/buy.blocked(:210/:215)
  - `health_monitor`(离线/接口健康两标题,锚点编码时 grep 定)
- **不做**:全量 call site 扫荡(机制先行,渐进);邮件/外推通道改结构化(只站内)

## 产出 3:POST /factors 扩 DSL 类型(13号#2)

- **文件**:`server/src/strategy_framework/factor.py`+`server/src/web_api/routes/strategy.py`+`web/src/views/Factors.vue`
- **后端**(盲审 A/B-P0:**DSLFactor 构造零校验**——parse 在 compute():479 每根 bar 跑,坏表达式静默入库实盘才爆):抽 factor.py:479-488 的 AST 预处理为独立 `validate_dsl_expr(expr)->int`(parse+窗口函数黑名单+**返回最大窗口 n**,ValueError 出错)——**register 与 load_factors_from_db 两处调用**;`register_custom_factor(..., ftype="python")`:ftype="dsl" 时 validate(抛 ValueError→route 现有 400 化)+needs_history=validate 返回的最大窗口 n(盲审 B-P1:补 0 会误标静态因子+static_only 选股资格埋雷);factor_def 加 `type` 列(迁移并入 0059,**同迁移双 ALTER**;INSERT:247/UPDATE:241 同步带 type——盲审 A-P2);`load_factors_from_db` 按 type 分流:dsl→validate(坏表达式启动期跳过并 warning,不炸进程)→注册 `functools.partial(DSLFactor, name, code)`(entry["cls"]() 零参调用语义不变,strategy.py:180/581 消费面零改;needs_history=n)
- **路由**:`create_factor_api` 透传 ftype;GET factors 列表带 type;`/api/factors/preview` 不动(dsl 试算=另事,preview 走 python code 编译,dsl 表达式试算待前端用回测链)
- **前端 Factors.vue**:新建对话框加类型 radio(python|dsl);dsl 时字段标签"表达式"+placeholder `mean(close,20) / close - 1`+**禁用"校验/试算"按钮**(走 python 编译链必报错,盲审 B-P2)+命名提示"禁 `dsl:` 前缀"(strategy.py:175 内联路径劫持,盲审 B-P2);**后端 register 同步拒 `dsl:` 前缀名**;列表 type badge;被引用/权重列不动
- **运行时**:策略 factors 引用 DSL 因子=按名引用(registry 命中 partial);内联 `dsl:` 前缀路径保留(现行为)

## 验收
1. `pytest tests/ -q` 全绿+新增测试(notify code 透传/DSL 注册校验/load 分流)
2. `smoke-web.sh` 本地跑全绿(dev-start 环境)
3. web build 绿;Factors.vue 手工路径:建 dsl 因子→列表 badge→策略引用;**DSL 因子实算出值**(建 mean(close,20)/close-1→preview 或回测链出数——盲审 B:原验收恰放过 P0 类问题);坏表达式建→400 非 500
4. 通知列表 code chip 显示;未打码旧通知无 chip 不报错

## 风险
- notifications ALTER 在产线(小表,锁秒级);factor_def ALTER 同(行数少)
- partial 注册的 DSL 因子:registry entry 的 needs_history=validate 返回的最大窗口 n(盲审 B-P1:补 0 会误标静态+static_only 选股资格)
- smoke 脚本凭证:dev=admin/admin123(**实盘模式随机密码,脚本在 prod 无效——文档标注仅 dev/staging 用**)

## 参考
- docs/reference/web-design/15-复审与方案校准.md §5(校准路径)
- docs/architecture/模块契约/alert_notify.md(如无则以 notify.py 现注释为准)
