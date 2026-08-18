# 08 - Web 管理后台

## 1. 目的

平台唯一的可视化管控入口。**只做展示与配置，不做交易计算**——所有交易/风控/数据逻辑在后端服务，Web 只调 API。可定制展示 A 股分析结果、策略启停、持仓盈亏、风控、日志。

## 2. 职责

1. **账户管理**：券商/XTP/币安/OKX API 加密存储、启停。
2. **策略管理**：多模型启停、参数在线修改、进程监控。
3. **A 股分析看板**：每日选股、日线评级、分钟级实时研判（WS 推送）。
4. **实盘交易看板**：可转债/ETF/合约持仓、订单、盈亏、成交记录。
5. **风控中心**：全局止损、单日亏损限制、一键熔断。
6. **日志告警**：运行日志、报错、交易日志可视化。
7. **自然语言查询**：Web 聊天框 → LLM 网关 → function calling 查持仓/盈亏/策略状态（下单类工具不注册）。

## 3. 边界与非目标

- **不做**：交易引擎逻辑、风控规则计算、数据清洗——都调后端 API。
- **非目标**：不做公开注册（admin 邀请制）；不做多租户（单系统多用户 RBAC 四角色）。
- **后端** FastAPI（Python），**前端** Vue3 + Element Plus。

## 4. 依赖

- FastAPI（后端，REST + WebSocket）
- Vue3 + Element Plus（前端）
- 各后端模块的 API：策略框架、数据中台、风控、LLM 网关、告警
- PostgreSQL（业务数据读展示）、Valkey（实时行情 WS 源）

## 5. 接口（后端 API 边界）

### 5.1 账户
```
GET    /api/account                 # 列账户（密钥不返回明文）
POST   /api/account                  # 新增（密钥加密存储）
POST    /api/account/{id}             # 改
DELETE /api/account/{id}
```

### 5.2 策略
```
# 策略配置（配方，不绑标的）
GET    /api/strategy                 # 列策略配置
POST   /api/strategy                 # 新建（写 strategy_config）
POST    /api/strategy/{id}            # 改配置（含 parameter_defs 参数定义）
POST   /api/strategy/{id}/start      # 旧路径（兼容）
POST   /api/strategy/{id}/stop       # 旧路径（兼容）
POST   /api/strategy/validate-python # Python 代码 AST 校验
POST   /api/strategy/validate-params # parameter_defs + 参数值校验

# 实盘任务（策略与标的分离，一标的一进程）
GET    /api/live-task                # 列实盘任务
POST   /api/live-task                # 创建（选策略+标的+任务参数值，构建 strategy_snapshot）
POST   /api/live-task/{id}/start     # 启动 systemd quant-strategy@<id>
POST   /api/live-task/{id}/stop
DELETE /api/live-task/{id}           # 删（仅 stopped/error）

# 因子库（预置 + 自定义 DB 因子）
GET    /api/factors                  # 列因子（含 needs_history/is_custom）
POST   /api/factors                  # 新建自定义因子（Python 代码）
POST    /api/factors/{name}           # 改
DELETE /api/factors/{name}           # 删
POST   /api/factors/validate         # 因子代码校验

# 回测（多标的 + per-symbol 参数）
POST   /api/backtest                 # 创建（symbols/pool_id + params + symbol_params）
GET    /api/backtest                 # 列表
GET    /api/backtest/{run_id}        # 详情
GET    /api/backtest/{run_id}/summary # 汇总（平均+排名）
```

### 5.3 交易/持仓/盈亏
```
GET    /api/position                 # 当前持仓
GET    /api/pnl?from=&to=            # 盈亏曲线
GET    /api/orders?from=&to=         # 订单/成交
```

### 5.4 A 股分析
```
GET    /api/astock/selection?date=
GET    /api/astock/analysis?symbol=
WS     /ws/astock/realtime?symbol=   # 分钟级实时研判
```

### 5.5 风控
```
GET    /api/risk/state
POST   /api/risk/halt                # 一键熔断
POST   /api/risk/resume
POST    /api/risk/rules               # 改风控规则
GET    /api/risk/log                 # 风控触发记录
```

### 5.6 日志/告警
```
GET    /api/log?level=&module=&from=&to=
GET    /api/notifications?status=&limit=   # 通知中心（role 过滤类别）
POST   /api/notifications/ack-all            # 全部确认
GET    /api/email-outbox                      # 邮件发件箱状态
```

### 5.7 自然语言查询
```
POST   /api/chat                     # {message} → 转发 LLM 网关 + 只读工具
WS     /ws/chat                      # 流式返回
```
> Web 聊天**只开放读类工具**（查持仓/盈亏/策略/研判）。操作类动作（熔断/恢复/停策略）走上方按钮 API（`/api/risk/halt` 等），不经 LLM。操作类经 LLM 的入口只在飞书（11），带交互确认。

### 5.8 实时推送（WS）
```
WS     /ws/market?symbol=            # 实时行情（源：Valkey）
WS     /ws/pnl                       # 实时盈亏
WS     /ws/strategy/{id}             # 单策略实时态
```

### 5.9 认证与角色
```
POST   /api/auth/login               # {username, password} → JWT
POST   /api/auth/logout
GET    /api/auth/me                  # 当前用户+角色
GET    /api/user                     # 列用户（Admin）
POST   /api/user                     # 建用户（Admin）
POST    /api/user/{id}                # 改角色/密码（Admin）
DELETE /api/user/{id}                # 删用户（Admin）
GET    /api/audit?actor=&action=&from=&to=   # 审计日志（Admin）
```

**角色与权限矩阵**（RBAC，非多租户；所有用户共享数据，仅权限分层）：

| 操作 | Viewer | Analyst | Trader | Admin |
|---|:--:|:--:|:--:|
| 查看持仓/盈亏/研判/日志 | ✅ | ✅ | ✅ |
| 启停策略 / 改策略参数 | ❌ | ✅ | ✅ |
| 一键熔断 emergency_halt | ❌ | ✅ | ✅ |
| 恢复交易 resume | ❌ | ❌ | ✅ |
| 改风控规则 | ❌ | ❌ | ✅ |
| 管理账户/密钥 | ❌ | ❌ | ✅ |
| 用户管理 / 系统配置 | ❌ | ❌ | ✅ |

实现：每个 endpoint 加权限装饰器（`@require_role("admin")` / `@require_role("operator","admin")`）；JWT 携带 role，中间件校验。所有 mutation 写 `audit_log(actor, action, target, ts, detail)`。

**账号与角色**：支持多个登录账号，每个账号绑一个角色（`user` 表 `role` 字段）；同一角色可有多个账号（如多个 Trader、多个 Viewer），无数量限制。Admin 可建/改/禁用/删账号。非多租户——所有账号共享同一套数据与交易系统，差异仅在角色权限。

## 6. 页面结构（前端）

| 页面 | 内容 |
|---|---|
| 个人中心 `/profile` | 头像（点头像更换：36系统图标/上传裁剪）、昵称、改密码、注销（所有角色可见） |
| 账户管理 | 用户列表（角色/邮箱/昵称/状态/上次登录/邀请人）+ 邀请发送 + 邀请记录（撤销）+ API 密钥 |
| 策略管理 | 策略配方（因子+权重+DSL/Python代码+参数定义）+ 实盘任务（选策略+标的+参数值）+ 回测（多标的+per-symbol 参数） |
| A 股分析看板 | 选股结果、评级、分钟研判实时 |
| 实盘交易看板 | 持仓/订单/盈亏曲线/成交 |
| 风控中心 | 全局/分市场规则、一键熔断按钮、三级开关、触发日志 |
| 日志告警 | 运行/报错/交易日志 + 通知历史 + 邮件发件箱（指数退避状态） |
| AI 助手 | 聊天框，自然语言查平台 |
| 系统配置 | 邮件发信配置（SMTP 整组+测试）+ 通用配置项 |
| 顶栏 | 头像+昵称下拉（个人中心/退出）+ 语言切换 + 通知铃铛（按角色过滤类别） |

策略配置页含**因子多选+权重+DSL/Python 代码框（双模式）+ 参数定义编辑器**。实盘任务页读策略 `parameter_defs` **动态生成参数表单**，选标的（直接/池/池子集）+ 填任务参数值。回测页支持 per-symbol 参数覆盖（高级模式）。

## 6.5 用户管理（邀请制完整链路）

### 邀请流程
```
admin 填邮箱（语言=当前界面语言）→ POST /api/auth/invite
→ user_tokens 落 invite token（72h 有效, 含 revoked 撤销标记）
→ 邮件走发件箱（指数退避重发）→ 被邀请者点链接
→ /register?token=... 验证 → 设用户名+密码（≥8位字母数字+二次确认）
→ 条款强制阅读（全语言堆叠, 滚到底解锁确认）→ 开通（默认 Viewer）
→ 自动发开通通知邮件（附登录链接+条款全文）
```

### 账户保护（两条不变量）
- **管理页**：不能动自己（`guard_user_mutation`，删/改角色/禁用均拦截）。末位 admin 无需显式规则——user_mgmt 仅 admin 持有 + 不能动自己 ⇒ 操作者若是另一 admin 则目标非末位，不可达
- **自助注销**：唯一启用的 admin 不能注销自己（`guard_self_deactivate`，此路径真实可达）

### 软删除/注销
- admin 删除和用户自助注销共用 `soft_delete_user()`：deleted_at + email/nickname 置空 + username 加后缀释放 + 头像文件清理
- 登录/列表过滤已注销；自助注销额外做 JWT token 拉黑

### 头像系统
- `users.avatar_url` 三态：空=按昵称 hash 从 36 图标确定性选 / `/icons/icon_NN.png`=系统图标 / `/api/static/avatars/user_{id}.jpg?t=`=上传
- 上传：vue-cropper 裁剪 1:1 → Pillow 256px JPEG → 覆盖式文件名（`deploy-server.sh` EXCLUDES 排除 `static/avatars/` 防 rsync --delete）

### 邮件发件箱（email_outbox）
- 持久化+指数退避（1→2→4→8→16→30min，6 次耗尽标 failed → 通知中心铃铛）
- 三封邮件（邀请/重置/开通）全走发件箱；接口 BackgroundTasks 后台发送
- SMTP 配置走 system_config（smtp_* 五项，密码 Fernet 加密，弃 .env）；测试邮件按钮

## 7. 安全

- 仅内网/VPN 访问，防火墙仅开必要端口。
- **RBAC**（单系统多用户，非多租户）：Viewer/Analyst/Trader/Admin 四角色，JWT 认证（24h + jti 黑名单，logout 即失效）+ endpoint 权限装饰器；数据共享不加 user_id 隔离。
- **登录**：用户名或邮箱（含 @ 按 email 查）；被禁账户返回 ACCOUNT_DISABLED 错误码（不误报密码错）。
- API 密钥本地加密存储（Fernet），前端永不返回明文；**仅 Admin 管理**。
- 密码策略：≥8 位含字母数字（前后端统一校验）；bcrypt 哈希。
- 自然语言查询的工具集**按角色过滤 + 白名单只读**。
- 操作审计：所有 mutation 写 `audit_log(actor, ...)`。
- **错误码化**：`ApiError(status, CODE, 中文兜底)` → `{detail, code}`；前端 `apiErr(e)` 优先本地化。

## 8. 与其它模块交互

- **策略框架（02）**：读写 `strategy_config`，启停策略，WS 推运行态。
- **数据中台（06）**：读 K 线/选股结果/研判；WS 推实时行情。
- **风控（07）**：熔断按钮、规则配置、触发日志。
- **LLM 网关（01）**：`/api/chat` 转发 + 只读工具注册。
- **告警（10）**：展示告警历史。
- **调度层（09）**：展示任务状态。
- **各交易引擎**：读持仓/订单/盈亏展示。

## 9. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 前后端分离 | Vue3+FastAPI | vnpy-webstrader 可参考但自建定制更灵活 |
| 无交易计算 | 只展示配置 | 交易逻辑在后端，Web 不背锅 |
| 实时 | WebSocket | 行情/盈亏/策略态实时 |
| 访问 | 内网/VPN | 私有化，不公开 |
| 密钥 | 加密存储+不返明文 | 安全 |
| 策略配置 UI | 表单+因子选择+DSL编辑器 | 配置驱动模型 |
| 自然语言查询 | 转发 LLM 网关+只读工具 | 便利但权限锁死 |
| 国际化 | N 语言注册表（en 缺省） | 加语言=加条目零逻辑改动；条款全语言堆叠 |
