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
- **非目标**：不做公开访问，仅内网/VPN 访问；不做多用户权限（个人平台单用户）。
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
PUT    /api/account/{id}             # 改
DELETE /api/account/{id}
```

### 5.2 策略
```
# 策略配置（配方，不绑标的）
GET    /api/strategy                 # 列策略配置
POST   /api/strategy                 # 新建（写 strategy_config）
PUT    /api/strategy/{id}            # 改配置（含 parameter_defs 参数定义）
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
PUT    /api/factors/{name}           # 改
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
PUT    /api/risk/rules               # 改风控规则
GET    /api/risk/log                 # 风控触发记录
```

### 5.6 日志/告警
```
GET    /api/log?level=&module=&from=&to=
GET    /api/alert?from=&to=
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
PUT    /api/user/{id}                # 改角色/密码（Admin）
DELETE /api/user/{id}                # 删用户（Admin）
GET    /api/audit?actor=&action=&from=&to=   # 审计日志（Admin）
```

**角色与权限矩阵**（RBAC，非多租户；所有用户共享数据，仅权限分层）：

| 操作 | Viewer | Operator | Admin |
|---|:--:|:--:|:--:|
| 查看持仓/盈亏/研判/日志 | ✅ | ✅ | ✅ |
| 启停策略 / 改策略参数 | ❌ | ✅ | ✅ |
| 一键熔断 emergency_halt | ❌ | ✅ | ✅ |
| 恢复交易 resume | ❌ | ❌ | ✅ |
| 改风控规则 | ❌ | ❌ | ✅ |
| 管理账户/密钥 | ❌ | ❌ | ✅ |
| 用户管理 / 系统配置 | ❌ | ❌ | ✅ |

实现：每个 endpoint 加权限装饰器（`@require_role("admin")` / `@require_role("operator","admin")`）；JWT 携带 role，中间件校验。所有 mutation 写 `audit_log(actor, action, target, ts, detail)`。

**账号与角色**：支持多个登录账号，每个账号绑一个角色（`user` 表 `role` 字段）；同一角色可有多个账号（如多个 Operator、多个 Viewer），无数量限制。Admin 可建/改/禁用/删账号。非多租户——所有账号共享同一套数据与交易系统，差异仅在角色权限。

## 6. 页面结构（前端）

| 页面 | 内容 |
|---|---|
| 账户管理 | API 密钥加密存储、启停 |
| 策略管理 | 策略配方（因子+权重+DSL/Python代码+参数定义）+ 实盘任务（选策略+标的+参数值）+ 回测（多标的+per-symbol 参数） |
| A 股分析看板 | 选股结果、评级、分钟研判实时 |
| 实盘交易看板 | 持仓/订单/盈亏曲线/成交 |
| 风控中心 | 全局/分市场规则、一键熔断按钮、触发日志 |
| 日志告警 | 运行/报错/交易日志、告警历史 |
| AI 助手 | 聊天框，自然语言查平台 |

策略配置页含**因子多选+权重+DSL/Python 代码框（双模式）+ 参数定义编辑器**。实盘任务页读策略 `parameter_defs` **动态生成参数表单**，选标的（直接/池/池子集）+ 填任务参数值。回测页支持 per-symbol 参数覆盖（高级模式）。

## 7. 安全

- 仅内网/VPN 访问，防火墙仅开必要端口。
- **RBAC**（单系统多用户，非多租户）：Viewer/Analyst/Trader/Admin 四角色（2026-08-02 Operator 拆 Trader+Analyst），JWT 认证 + endpoint 权限装饰器；数据共享不加 user_id 隔离。
- API 密钥本地加密存储（AES + 密钥在环境变量），前端永不返回明文；**仅 Admin 管理**，Operator/Viewer 不见。
- 自然语言查询的工具集**按角色过滤 + 白名单只读**，下单类工具不在网关注册范围（01 文档已锁）。
- 操作审计：所有 mutation（启停策略/改参数/熔断/改风控/改密钥）写 `audit_log(actor, ...)`，Admin 可查。

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
| 国际化 | vue-i18n 按浏览器语言自动切换中/英文 | 跟终端语言习惯，日志统一英文 |
