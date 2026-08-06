# 11 - 飞书/Lark 对接层

## 1. 目的

通过飞书机器人与平台交互的**外部 IM 通道**：AI 动态查询（自然语言问持仓/盈亏/策略状态）+ 紧急处理等设计好的操作接口（一键熔断、恢复、停策略）。让你在手机上随时查询和干预平台，不依赖 Web 后台。

与 08 Web 的 `/api/chat` 是**两个入口共用一套工具**：Web 聊天只读，飞书额外开放操作类工具（带确认）。

## 2. 职责

1. **消息接收**：飞书事件订阅 / Webhook 接收用户消息。
2. **路由到 LLM 网关**：消息 + 工具集 → LLM 网关 `chat(tools=...)` → function calling。
3. **工具调用执行**：读类工具直接执行返回；操作类工具走**确认流程**。
4. **交互确认**：操作类动作（熔断/恢复/停策略）先发飞书确认卡片，用户点确认才执行。
5. **用户鉴权**：只有授权的飞书用户/群可发指令，非授权一律忽略。
6. **全审计**：每条指令、每次工具调用、每个确认都记 `audit_log`。
7. **结果回推**：LLM 回复 + 工具结果通过飞书消息/卡片回推。

## 3. 边界与非目标

- **永不暴露交易下单工具**：`place_order` / `cancel_order` 不在工具白名单——和 01 一致，AI 层不握方向盘。
- **操作类仅限安全操作**：熔断/恢复/停策略/启策略，**不含**改策略参数/改风控规则（这些走 Web，避免误操作）。
- **非目标**：不做飞书原生应用市场发布；不做多租户。
- **底层** 飞书开放平台 SDK（Python，`lark-oapi`）是第三方，本模块做对接与安全层。

## 4. 依赖

- 飞书开放平台：自建应用，事件订阅 + 消息发送 + 交互卡片
- `lark-oapi`（飞书 Python SDK，第三方）
- LLM 网关（01）：`chat(tools=...)`，工具注册在网关层
- 风控（07）：`emergency_halt` / `resume` / `risk_state`
- 策略框架（02）：`strategy_start/stop/status`
- 数据中台（06）：`get_position/pnl/analysis`
- 告警（10）：操作结果可同步推送
- FastAPI：接收飞书 Webhook

## 5. 接口

### 5.1 飞书 Webhook（FastAPI 端点）
```
POST /lark/webhook        # 飞书事件订阅回调
POST /lark/card/callback  # 交互卡片回调（确认按钮）
```
**⚠️ 3 秒超时约束**：飞书事件订阅要求 Webhook 在 **3 秒内**返回 HTTP 200，否则判定失败并重试（导致重复触发 LLM/重复动作）。LLM 调用 5-30s 必超时。因此 `/lark/webhook` 收到消息后**立即**返回 `{"code":0}`，把 LLM 任务丢给 **Celery 后台任务**（或后台线程），由后台任务处理完后**主动调用飞书"发送消息"API** 推回结果。确认卡片同理：后台任务发卡片，用户点确认触发 `/lark/card/callback`，回调也立即 200 后异步执行。

### 5.2 工具白名单（分层，在 LLM 网关注册）

**读类（直接执行，无需确认）：**
```python
tools_readonly = [
    query_position,        # 查持仓
    query_pnl,             # 查盈亏
    query_strategy_status, # 查策略状态
    get_astock_analysis,   # 查A股研判
    query_risk_state,      # 查风控状态
    query_orders,          # 查今日订单
]
```

**操作类（必须交互确认）：**
```python
tools_operational = [
    emergency_halt,        # 一键熔断
    risk_resume,           # 恢复交易
    strategy_stop,         # 停某策略
    strategy_start,        # 启某策略
]
```

**永不注册：** `place_order` / `cancel_order` / `modify_risk_rule` / `modify_strategy_params`。

### 5.3 确认流程
```
LLM 调 emergency_halt(reason="...")
  └> 网关拦截 operational 工具 → 发飞书确认卡片:
     "确认执行【一键熔断】? 原因: ...  [确认] [取消]"
  └> 用户点 [确认] → 卡片回调 → 网关执行 emergency_halt() → 结果回推
  └> 用户点 [取消] / 超时(60s) → 取消，记审计
```

## 6. 用户鉴权与角色映射

- 飞书自建应用配置：只响应白名单 `user_id` / `chat_id`（授权用户/群）。
- 非授权用户发消息 → 静默忽略 + 记 `audit_log`（level=warn）。
- **角色映射**：每个授权飞书用户绑定平台角色（Viewer/Operator/Admin），决定可用工具层（读类 / 读+操作类 / 全部）——与 08 Web RBAC 一致。
- **操作类确认人按角色**：操作类工具的确认人默认为发起人本人（Operator/Admin）；`resume`（恢复交易）仅 Admin 可确认；`emergency_halt` Operator+Admin 可确认（安全冗余）。
- **工具按角色过滤**：`/lark/webhook` 收到消息时按发起人角色，只把对应层工具注册进 LLM 网关（详见 01）。
- 全程 `audit_log` 记 actor + 工具调用 + 确认结果。
- **多语言**：检测飞书用户 `locale` 设置（zh_cn/en），LLM 回复/确认卡片按此语言输出。无 locale 信息的用户默认英文。

## 7. 数据流

```
飞书用户消息 ─> /lark/webhook ─> 鉴权(白名单) ─> 立即回 {"code":0}（3s 内）
  └> [后台 Celery 任务] LLM网关.chat(messages, tools=按角色过滤)
       ├> LLM 调读类工具 ─> 直接执行 ─> 结果回 LLM ─> 主动调飞书发消息API回推
       └> LLM 调操作类工具 ─> 网关拦截 ─> 后台发确认卡片 ─> 用户点确认
            ─> /lark/card/callback（立即200） ─> [后台任务] 执行 ─> 飞书回推结果
  全程 audit_log 记录
```

## 8. 安全设计

| 层 | 措施 |
|---|---|
| 渠道 | 飞书 Webhook 签名校验（防伪造） |
| 用户 | user/chat 白名单 |
| 工具 | 白名单分层，下单类永不注册 |
| 操作 | 交互卡片确认 + 超时失效 |
| 审计 | 全量 `audit_log`，Web 可查 |
| 限流 | 单用户每分钟指令数上限，防滥用 |

## 9. 典型场景

| 你说 | 平台做 |
|---|---|
| "现在持仓多少？今天盈亏？" | LLM 调 query_position/query_pnl → 汇总回飞书 |
| "BTC 策略现在什么状态？" | query_strategy_status → 回状态 |
| "紧急停止所有交易" | emergency_halt → 发确认卡片 → 你确认 → 熔断 → 回结果 |
| "把可转债双低策略停了" | strategy_stop(id=...) → 确认 → 执行 |
| "今天 A 股选了什么？" | get_astock_analysis → 回选股结果 |

## 10. 与其它模块交互

- **LLM 网关（01）**：核心依赖，工具注册在网关层，确认机制在网关。
- **风控（07）**：`emergency_halt`/`resume` 的一个外部触发入口（与 Web 按钮并行，安全冗余）。
- **策略框架（02）**：`strategy_start/stop/status`。
- **数据中台（06）**：查询持仓/盈亏/研判。
- **告警（10）**：操作结果可同步推送其它渠道。
- **Web 后台（08）**：`audit_log` 展示；飞书与 Web 是平行的两个交互入口。

## 11. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 独立模块 vs 并入 Web | 独立模块 | IM 通道特性不同（确认卡片、鉴权、限流），单独清晰 |
| 工具复用 | 与 Web chat 共用读类工具 | 不重复定义 |
| 操作类 | 飞书开放，Web 只读 | 手机随时干预，但带确认 |
| 下单类 | 永不注册 | AI 不握方向盘，三重隔离 |
| 确认 | 交互卡片+超时 | 防误触，尤其熔断 |
| 鉴权 | 用户/群白名单+签名 | 防伪造+防越权 |
