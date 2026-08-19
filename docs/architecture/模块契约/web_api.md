# 模块契约 · web_api（Web 后端 API）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（6 大接口 + 数据结构）。本文件不重复数据结构定义，只列"本模块暴露什么端点"。

## 职责
FastAPI Web 后端，~85 端点。RBAC 认证（JWT + 四角色）+ 策略/持仓/风控/实盘开关/LLM/飞书/同步/回测业务端点 + 平台化管理端点（数据源/通道/任务/规则/模型）。
启动 `uvicorn src.web_api.main:app --port 8000`；前端 Vue3 调用；飞书 router 内嵌。

## 文件结构
```
server/src/web_api/
├── main.py           # FastAPI app + ~85 端点（1494 行）
├── auth.py           # JWT + RBAC + 用户管理 + 邀请/重置 + audit_log
├── crypto_utils.py   # AES encrypt/decrypt（凭证加密）
├── email_service.py  # 邀请/重置邮件发送
└── __init__.py
```
> 飞书端点经 `src.feishu_bot.router` 以 `app.include_router` 内嵌（非本目录文件，但 URL 空间归 web_api）。

---

## 一、public API（端点，稳定）

### RBAC 守卫（auth.py，所有受保护端点用）
```python
require_role(*roles) -> Depends    # 角色 gate（viewer/analyst/trader/admin）
require_perm(perm: str) -> Depends  # 权限 gate（user_mgmt/strategy_control/halt/...）
PERMISSIONS: dict[str, set[str]]    # 角色 -> 权限集
# 返回 payload = {"sub": user_id, "username": , "role": }
```

### 端点分组（27 组，~85 路由）

| 组 | 前缀 | 方法 | RBAC | 说明 |
|---|---|---|---|---|
| 系统 | `/health` | GET | 无 | 健康检查 |
| 认证 | `/api/auth/*` | POST/GET | viewer+（部分 user_mgmt） | login/me/logout/invite/invite/verify/register/forgot/reset/change-password |
| 用户管理 | `/api/user` | POST/GET | user_mgmt（Admin） | create/list 用户 |
| 策略 | `/api/strategy` `/api/strategy/{sid}/{start,stop,verify}` | GET/POST/POST/POST | viewer+ / strategy_control | CRUD + 启停 + 回测验证标记 |
| 持仓/盈亏/订单/账户 | `/api/position` `/api/pnl` `/api/orders` `/api/account` `/api/account/{aid}` | GET | viewer+ | 读查询（XTP query） |
| 日志/告警 | `/api/log` `/api/alert` | GET | viewer+ | 系统/告警日志 |
| **聊天（AI）** | `/api/chat` | POST | viewer+ | `gateway.chat(tools=READ_TOOLS, caller="web_chat")` |
| **WS 流式（AI）** | `/ws/chat` | WS | viewer+ | `gateway.chat_stream`（D1） |
| A股分析 | `/api/astock/selection` | GET | viewer+ | `DailySelectionEngine.run` |
| 风控 | `/api/risk/state` `/api/risk/halt` `/api/risk/resume` | GET/POST | viewer+ / halt | RiskControl 查/熔断/恢复 |
| 实盘开关 | `/api/live-trading` `/api/live-trading/{market}` | GET/POST | viewer+ / strategy_control | 三级第二级（5 分项） |
| LLM模型管理 | `/api/llm-models` `/api/llm-models/{mid}/{test}` | GET/POST/POST/DELETE | admin | CRUD + 测试 + reload_models |
| 飞书 | `/api/feishu/{list,connect,status,{fid}/{start,stop,test}}` `/api/feishu/{fid}` | GET/POST/POST/DELETE | admin | 多机器人管理 |
| 数据同步 | `/api/sync/{config,config/{sid},trigger/{sid}/progress,symbols/{sid},symbol/{sid}/{ts_code}/backfill,all/{sid}/progress,data/{sid},log}` | GET/POST/POST/DELETE | viewer+ / strategy_control | 同步配置 + 触发 + 标的 + 进度 |
| K线 | `/api/kline/{symbol}` | GET | viewer+ | `get_bars` + `to_vt_symbol` |
| 筛选 | `/api/screen/{astock,cb,etf}` | GET | viewer+ | 标的筛选（daily_basic） |
| **LLM用量** | `/api/llm-usage/summary` | GET | viewer+ | 今日/本月/7天趋势（llm_usage 聚合） |
| 数据源管理 | `/api/data-sources` `/api/data-sources/{dsid}/{test}` | GET/POST/POST/DELETE | admin | PT3（data_source_config CRUD） |
| 后台任务 | `/api/tasks` `/api/tasks/{task_id}/{terminate,force-delete}` `/api/tasks/detect-stuck` | GET/POST | viewer+ | PT1（list/get/终止/强删/卡死检测） |
| 消息通道 | `/api/channels` `/api/channels/{cid}/{test}` | GET/POST/POST/DELETE | admin | PT4（channel_config CRUD） |
| 交易通道 | `/api/brokers` `/api/brokers/{bid}/{test}` | GET/POST/POST/DELETE | admin | PT5（broker_config CRUD） |
| 风控规则 | `/api/risk-rules` `/api/risk-rules/{types,{rid}}` | GET/POST/POST/DELETE | admin | PT6（risk_rules CRUD） |
| 因子 | `/api/factors` `/api/factors/{name}` `/api/factors/validate` | GET/POST/POST/DELETE | viewer+ / strategy_control | 因子 CRUD（预置+自定义）+ 代码校验 |
| 策略校验 | `/api/strategy/validate-python` `/api/strategy/validate-params` | POST | analyst+ | Python 代码 AST 校验 + parameter_defs 校验 |
| **实盘任务** | `/api/live-task` `/api/live-task/{tid}/{start,stop}` `/api/live-task/{tid}` | GET/POST/DELETE | viewer+ / strategy_control | 策略与标的分离（live_task CRUD，一标的一进程） |
| 对账 | `/api/reconcile` | GET | viewer+ | `scheduler.reconcile_three_books`（三账） |
| 审计 | `/api/audit` | GET | admin | audit_log 查询 |
| 数据完整性 | `/api/data-integrity` | GET | viewer+ | A3（freq 1D/1min/5min + 标的数） |
| 数据源用量 | `/api/data-source-usage` | GET | viewer+ | A4（data_source_usage 看板） |
| 回测 | `/api/backtest` `/api/backtest/{run_id}` `/api/backtest/{run_id}/{symbol}/stream` `/api/backtest/{run_id}/summary` | POST/GET | analyst+ | B3（创建含 symbol_params/列表/详情/SSE 流/汇总） |

### Pydantic 模型（main.py，请求体）
`LoginReq` / `UserCreate` / `StrategyConfig` / `InviteReq` / `RegisterReq` / `ForgotReq` / `ResetReq` / `ChangePwdReq` / `ChatReq`(message) / `LLMModelReq` / `FeishuUpdateReq` / `DataSourceReq` / `ChannelReq` / `BrokerReq` / `RiskRuleReq`

### auth.py public API
```python
create_jwt(user_id, username, role) -> str
authenticate(username, password) -> dict | None      # 返回 user 行
create_user(username, password, role) -> int         # 抛 ValueError（重复）
require_role(*roles) / require_perm(perm) -> Depends  # 守卫
audit_log(username, action, target="", detail="")    # 写 audit_log
ensure_default_admin() -> bool                       # 启动建 admin/admin123
init_users_table() -> None                           # 幂等建 users
invite_user / register_user / forgot_password / reset_password / change_password / verify_token
PERMISSIONS: dict[str, set[str]]                     # 角色 -> 权限集
```

### crypto_utils.py
```python
encrypt(plaintext: str) -> str    # AES 加密（凭证入库前）
decrypt(ciphertext: str) -> str   # AES 解密（凭证出库后）
```

---

## 二、内部 API（不保证稳定）

- `startup()`：`init_users_table` + `ensure_default_admin`（`@app.on_event("startup")`）
- 各端点函数内 `from src.X import Y` 延迟 import（破循环依赖 + 减启动开销）
- 平台化端点用 `_REGISTRY`（data_source/broker/channel/risk_rule）枚举 provider/type 可选项
- `__import__("datetime")` 内联（个别端点避免顶部 import）

---

## 三、依赖（import 其他模块什么）

| 依赖 | 用途 |
|---|---|
| `src.data_platform.db.get_conn` | 几乎所有端点读写 DB |
| `src.data_platform.db.get_bars` | K线端点 |
| `src.data_platform.schema.to_vt_symbol` | K线/筛选端点 |
| `src.data_platform.settings.is_live_trading_enabled` | 实盘开关端点（第一级） |
| `src.data_platform.data_source._REGISTRY` | 数据源端点枚举 provider |
| `src.web_api.auth` / `crypto_utils` / `email_service` | 认证 + 加密 + 邮件 |
| `src.feishu_bot.router` / `.tasks` | 飞书 router 内嵌 + register 任务 |
| `src.strategy_framework.factor`（validate_strategy_factors / list_factors） | 策略校验 + 因子端点 |
| `src.strategy_framework.broker._REGISTRY` | 交易通道端点枚举 provider |
| `src.astock_analysis.DailySelectionEngine` | A股选股端点 |
| `src.risk_control.RiskControl` / `risk_rule._REGISTRY` | 风控端点 + 规则端点 |
| `src.llm_gateway.gateway` / `READ_TOOLS` | /api/chat + /ws/chat + llm-models |
| `src.data_sync.engine`（list_symbols/sync_symbol/backfill_symbol/delete_symbol） | 同步端点 |
| `src.scheduler.tasks`（sync_via_celery/sync_all_symbols/reconcile_three_books/backtest_run_task） | 同步触发 + 对账 + 回测 |
| `src.scheduler.app`（celery_app） | 同步进度查询 |
| `src.task_manager`（list_tasks/get_task/terminate_task/log_task/force_delete_task/detect_stuck） | 后台任务端点 |
| `src.alert_notify.channel._REGISTRY` | 消息通道端点枚举 provider |
| FastAPI / pydantic / psycopg / jwt（外部） | 框架 + 请求体 + DB + 令牌 |

> web_api 是**依赖汇聚点**（几乎所有业务模块）。改下游签名优先加默认值不破旧。

---

## 四、被谁调用（改端点签名/路径要同步改这些）

| 调用方 | 调什么 |
|---|---|
| 前端 Vue3（`web/src/api.js`） | 全部 `/api/*` 端点（fetch/axios） |
| 飞书 | `app.include_router(feishu_router)`（内嵌，非 HTTP 调用） |
| systemd `quant-web` 服务 | `uvicorn src.web_api.main:app`（启动入口） |

> 改端点路径/方法/请求体 -> 同步改 `web/src/api.js` + 前端对应组件。加端点不影响现有。

---

## 五、读写表

| 表 | 写（端点） | 读（端点） |
|---|---|---|
| `users` / `audit_log` | auth（create_user/invite/register + audit_log） | /api/user / /api/audit |
| `strategy_config` | /api/strategy POST/POST | /api/strategy GET + /api/live-task POST（读快照） |
| `live_task` | /api/live-task POST/DELETE + start/stop | /api/live-task GET + strategy_runner 启动读 |
| `factor_def` | /api/factors POST/POST/DELETE | /api/factors GET + load_factors_from_db |
| `live_trading_config` | /api/live-trading/{market} POST | /api/live-trading GET |
| `llm_model_config` | /api/llm-models CRUD | gateway._load_models_from_db（间接） |
| `llm_usage` | gateway._log_usage（间接） | /api/llm-usage/summary |
| `llm_budget` | D5 端点待加 | D5 告警逻辑待 |
| `feishu_config` | /api/feishu CRUD | feishu_bot 读 |
| `data_source_config` | /api/data-sources CRUD | get_data_source（间接） |
| `data_source_usage` | record_usage（间接） | /api/data-source-usage |
| `channel_config` | /api/channels CRUD | get_channel（间接） |
| `broker_config` | /api/brokers CRUD | get_broker（间接） |
| `risk_rules` | /api/risk-rules CRUD | risk_control._load_rules（间接） |
| `sync_config` / `sync_log` | /api/sync/* | /api/sync/* |
| `accounts` | - | /api/account |
| `tasks` / `task_logs` | task_manager（间接） | /api/tasks/* |
| `backtest_runs` / `backtest_symbols` / `pool_symbols` | /api/backtest POST | /api/backtest GET |
| `daily_basic` | - | /api/screen/* |
| `bar_1D` / `bar_1min` / `bar_5min` | - | /api/kline / 数据完整性 |
| `trade_cal` | - | is_trading_day（间接） |

---

## 六、不变量

- **RBAC 守卫**：所有受保护端点 `Depends(require_role(...))` 或 `Depends(require_perm(...))`；`/health` 无守卫
- **JWT**：`create_jwt` 签发；前端 `Authorization: Bearer <token>`；payload = `{sub, username, role}`
- **audit_log**：所有操作类端点（create/update/delete/halt/start/stop）写 audit_log
- **启动初始化**：`startup` 建 users 表 + 默认 admin（admin/admin123，需改密码）
- **CORS**：`allow_origins=["*"]`（开发），生产改具体域名
- **延迟 import**：端点函数内 `from src.X import Y`（破循环 + 减启动开销）
- **凭证加密**：所有 `_encrypted` 字段经 `crypto_utils.encrypt/decrypt`（data_source/broker/channel/llm_model/feishu）
- **平台化 _REGISTRY**：数据源/通道/规则端点用 `_REGISTRY.keys()` 枚举可选 provider/type
- **SSE/WS**：回测流 `/api/backtest/{run_id}/{symbol}/stream`（SSE）+ `/ws/chat`（WebSocket）
- **caller 标识**：/api/chat 传 `caller="web_chat"`（llm_usage 归因）

---

## 七、扩展指南

### 加新端点
1. `main.py` 内 `@app.{method}("/api/...")` + `Depends(require_role/require_perm)`
2. 请求体用 Pydantic BaseModel（顶部定义）
3. DB 读写用 `get_conn()`（`with` 退出还池）
4. 操作类写 `audit_log`
5. 同步改前端 `web/src/api.js` + 组件

### 加新 RBAC 权限
1. `auth.PERMISSIONS[role].add(perm)`
2. 端点用 `Depends(require_perm(perm))`

### 加新平台化配置端点（如新通道类型）
1. 对应接口 `_REGISTRY` 注册实现
2. migration 建 `*_config` 表（credentials_encrypted/params/enabled）
3. main.py 加 CRUD 端点（参照 /api/channels 模式）

### D4 日志归因 / D5 预算端点（待加）
- D4：`/api/log/analyze`（POST，传日志片段 -> gateway.chat 归因，caller="log_analyze"）
- D5：`/api/llm-budget`（GET/POST）+ 告警逻辑（llm_usage 聚合 vs llm_budget 阈值 -> MessageChannel）
- 两者均加在 main.py，参照 `/api/chat` + `/api/llm-usage/summary` 模式

---

## 修订记录
- 2026-08-10 初版（基于代码核实：main.py 路由 grep 85 条 + import 依赖 + DB 表操作 + 抽样端点 chat/llm-usage/auth/strategy）
- 2026-08-11 加 live_task（策略与标的分离）+ 因子 CRUD（factor_def）+ validate-params/validate-python + 回测 symbol_params

> ⚠️ 2026-08-17 语义变更（WAL 时序/order_prefix/fail-closed/verify 证据门禁等）：见 `docs/architecture/接口契约.md` 末节「今日语义变更」。


## 增量（2026-08-19 链条打磨）
- 新端点：`POST /api/factors/preview`（试算）/ `GET /api/help/{topic}`（指导书 md）/ backtest 详情 v2 形状（顶层四卡+task_id+symbols 对象数组）
- 因子删除引用守卫（409 FACTOR_IN_USE）；策略 create/update 品类校验（symbol 空按 type 推断）
- 本链 17 处 HTTPException→ApiError（14 码）；指导书内容源 server/docs/操作指导（rsync 部署）
