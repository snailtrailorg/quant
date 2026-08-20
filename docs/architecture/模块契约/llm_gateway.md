# 模块契约 · llm_gateway（LLM 网关）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md` §6 LLMProvider + §LLM 层（LLMResponse/Tool）。本文件不重复数据结构定义，只列"本模块暴露什么"。

## 职责
平台所有 AI 调用的**唯一入口**。国内模型 DeepSeek(主) + GLM(备)，按 `priority` 全局主备容灾 + 半开熔断 + 指数退避 + 工具白名单（RBAC 角色）+ 用量日志（llm_usage）。
运行期只用国内模型，**不接 Claude/OpenAI**；Claude Code 仅开发助手。LLM 回复按输入语言自然回复（已移除 lang 注入）。

## 文件结构
```
server/src/llm_gateway/
├── gateway.py     # LLMGateway 类 + 单例 gateway + 工具白名单
├── budget.py      # check_budget_alerts 预算告警（原寄生 web_api.main，2026-08-19 归位）
├── config.yaml    # failover 策略（retry_wait_s / circuit_breaker），模型配置已 DB 化
└── __init__.py    # 暴露 gateway 单例
```
（P3 回写 2026-08-20：补 budget.py）

---

## 一、public API（稳定，可跨模块调用）

### 单例与类型（gateway.py）
```python
gateway = LLMGateway()              # 模块级单例，import 即用
Role = Literal["viewer", "analyst", "trader", "admin"]

@dataclass Tool:        name: str; description: str; input_schema: dict
@dataclass LLMResponse: content: str; tool_calls: list[dict] | None; usage: dict; raw: Any
```

### gateway.chat（同步，主接口）
```python
gateway.chat(messages: list[dict], *,
             tools: list[Tool] | None = None,
             role: Role = "viewer",
             timeout: float = 30.0,
             retries: int = 1,
             caller: str | None = None) -> LLMResponse
# messages: OpenAI 格式 [{"role":"system/user/assistant","content":"..."}]
# tools: 传入时与角色白名单取交集（调用方只能缩小，不能越权）
# role: 决定可用工具范围（viewer/analyst=读类；trader=+halt+启停；admin=+resume）
# caller: 调用方标识，写 llm_usage.caller
#   实况取值（P3 回写 2026-08-20）：feishu / web_chat / daily_report / health_check /
#   astock（选股 LLM 研判）/ convertible_terms / stock_analyze（三档详情 AI 分析）/ log_analyze（D4）/ test
# 返回 LLMResponse；全失败返回 LLMResponse(content="", usage={"error":"所有 provider 不可用"})，不抛
```

### gateway.chat_stream（流式，D1 WS 用）
```python
async gateway.chat_stream(messages, *, tools=None, role="viewer",
                          caller=None) -> AsyncGenerator[str, None]
# yield 每个 chunk 的 delta.content；全失败 yield "（所有 provider 不可用）"
```

### gateway.reload_models
```python
gateway.reload_models() -> None    # Web 改 llm_model_config 后调，刷新 _models 缓存
```

### 工具白名单常量（gateway.py 顶部，定义"谁能用什么工具"）
```python
READ_TOOLS        # 读类 5 个：query_position/query_pnl/query_strategy_status/query_risk_state/get_astock_analysis
TRADER_TOOLS      # 交易类 3 个：emergency_halt/strategy_stop/strategy_start
ADMIN_TOOLS       # Admin 专属：risk_resume
FORBIDDEN_TOOLS   # 永不注册：{modify_risk_rule, modify_strategy_params}
OPERATIONAL_TOOLS # = TRADER_TOOLS + ADMIN_TOOLS（供外部判断"需确认卡片"）
```
- 角色映射：`viewer/analyst`=READ；`trader`=READ+TRADER；`admin`=READ+TRADER+ADMIN
- `_filter_tools(role, tools)` 实现：角色白名单 ∩ 传入 tools（传入 None 用全白名单）

---

## 二、内部 API（不保证稳定，改模块时才能动）

- `_load_models_from_db()`：从 `llm_model_config` 读 enabled 模型（按 priority 排序，api_key 解密）
- `_get_primary_fallback() -> (primary, fallback)`：取前两个（仅一个时 fallback=primary）
- `_check_input_chars(messages)`：入口 50 万字符拒（防恶意长输入）
- `_estimate_tokens(messages)`：字符数 ×1.5（中文保守估，无依赖）
- `_truncate_messages(messages, max_input)`：超限截断（保留 system + 最新一条，删中间）
- `_get_client(conf) -> (OpenAI, model)`：构建同步 OpenAI 客户端（timeout=30）
- `_is_circuit_open(provider)`：熔断检查 closed/open/half_open（半开放一个试探）
- `_record_fail(provider)` / `_reset_fail(provider)`：失败计数 + 半开状态（`_lock` 保护）
- `_filter_tools(role, tools)`：角色白名单 ∩ 传入 -> OpenAI tools 格式
- `_do_chat(...)` / `_do_chat_stream(...)`：实际调 LLM（primary→fallback + 指数退避 + 用量日志）
- `_parse_response(resp) -> LLMResponse`：解析 OpenAI 响应（content + tool_calls + usage）
- `_log_usage(provider, model, in, out, latency_ms, success, error_type, caller)`：写 llm_usage（失败不影响主流程）
- `_load_failover_config(path)`：读 config.yaml 的 failover 段（默认 retry_wait_s=2, fail_threshold=5, pause_s=300）

---

## 三、依赖（import 其他模块什么）

| 依赖 | 用途 |
|---|---|
| `openai.OpenAI` / `AsyncOpenAI`（外部） | 国内模型 OpenAI 兼容协议 |
| `yaml`（外部） | 读 config.yaml failover 段 |
| `dotenv`（外部） | 读 .env |
| `src.data_platform.db.get_conn` | 读 llm_model_config / 写 llm_usage |
| `src.quant_common.crypto.decrypt` | 解密 api_key_encrypted（2026-08-19 归位；原 `web_api.crypto_utils` 循环依赖已解）（P3 回写 2026-08-20） |

> 曾有 `llm_gateway` ⇄ `web_api`（crypto_utils/chat）循环依赖，靠函数内 import 打破——crypto 归位 `quant_common` 后解环；web_api 依赖 llm_gateway 方向保留。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `web_api.main` `/api/chat` | `gateway.chat(messages, tools=READ_TOOLS, role=, caller="web_chat")` |
| `web_api.main` `/ws/chat` | `gateway.chat_stream(...)`（D1 WS 流式） |
| `web_api.main` `/api/stock/{symbol}/analyze` | `gateway.chat(..., caller="stock_analyze")`（三档 AI 分析）（P3 回写 2026-08-20 补） |
| `web_api.main` `/api/log/analyze` | `gateway.chat(..., caller="log_analyze")`（D4 已实现，原 alert_notify 预留位撤销）（P3 回写 2026-08-20） |
| `web_api.main` `/api/llm-models/*` | 直接 SQL 读写 `llm_model_config`（不经 gateway）+ `gateway.reload_models()` |
| `astock_analysis.analysis` `enhance_with_llm` | `gateway.chat([...], role="viewer", caller="astock")` |
| `astock_analysis.convertible_terms` | `gateway.chat(..., caller="convertible_terms")`（D3）（P3 回写 2026-08-20 补） |
| `scheduler.tasks` 盘后报告/健康检查 | `gateway.chat(..., caller="daily_report"/"health_check")` |
| `scheduler.tasks.budget_alert_check` | `llm_gateway.budget.check_budget_alerts()`（本模块内）（P3 回写 2026-08-20 补） |
| `feishu_bot.bot` | `gateway.chat(..., role=<机器人角色>, caller="feishu")` |

> 改 `chat` / `chat_stream` 签名影响 web_api + astock + scheduler + feishu_bot。慎改，优先加参数带默认值。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `llm_model_config` | web_api（/api/llm-models 端点） | `gateway._load_models_from_db`（启动 + reload_models） |
| `llm_usage` | `gateway._log_usage`（每次调用；**SELECT 1 探测表存在**（gateway.py:383），无 CREATE TABLE——schema 全走迁移）（P3 回写 2026-08-20） | web_api `/api/llm-usage/summary`（用量看板） |
| `llm_budget` | web_api `/api/llm-budget` CRUD（已存在） | `budget.check_budget_alerts`（预算阈值 vs llm_usage 聚合；beat 1h）（P3 回写 2026-08-20：原"D5 端点待加"过时） |

> `llm_usage` / `llm_budget` 正式 schema 走 migration（0011 llm_usage / 0020 llm_budget）；gateway 内无运行时 DDL（P3 回写 2026-08-20：删"CREATE TABLE IF NOT EXISTS 兜底"旧述）。

---

## 六、不变量

- **单例**：`gateway = LLMGateway()` 模块级，import 即用；改配置后调 `reload_models()`
- **caller 必填建议**：调用方传 caller 标识，否则 llm_usage.caller=NULL（用量归因失效）
- **路由按 priority**：`llm_model_config.priority` 全局排序，primary=第一个，fallback=第二个（移除 tier 分级，2026-08-07）
- **熔断三态**：closed（正常）/ open（失败≥阈值，跳过 pause_s）/ half_open（pause 后放一个试探）
- **并发安全**：`_failed_counts`/`_last_fail_time`/`_half_open` 受 `self._lock`（threading.Lock）保护（单例+线程池并发）
- **工具角色白名单 ∩**：传入 tools 时与角色白名单取交集（调用方只能缩小，不能越权）；`FORBIDDEN_TOOLS` 永不注册
- **输入限制**：入口 50 万字符拒；超 `max_input_tokens` 截断（保留 system + 最新）；输出传 `max_tokens=max_output_tokens`
- **不抛异常**：全 provider 失败返回空 LLMResponse（content=""），调用方判空
- **usage 字段**：成功 `{input_tokens, output_tokens, model}`；失败 `{error: "..."}`；无 token 计数时 0

---

## 七、扩展指南

### 加新模型（如 GLM-5.2 / 新 DeepSeek）
1. Web `/api/llm-models` POST 配 `llm_model_config`（provider/model/api_key/base_url/priority/enabled）
2. 调 `gateway.reload_models()`（或重启）
3. **不改代码**（OpenAI 兼容协议，靠 priority 自动主备）

### 加新工具（如 query_convertible_terms）
1. `READ_TOOLS`（或 `TRADER_TOOLS`/`ADMIN_TOOLS`）加一行 `Tool(name=..., description=..., input_schema={...})`
2. 上层（web_api/feishu_bot）实现 tool 执行 handler
3. `_filter_tools` 自动按角色注入

### 加新 provider 协议（非 OpenAI 兼容，如 Anthropic 原生）
1. `_get_client` / `_do_chat` 内分支新协议（或抽象 provider adapter）
2. 评估是否破坏 OpenAI 兼容假设（当前国内模型都兼容，无需）

---

## 修订记录
- 2026-08-10 初版（基于代码核实：gateway.py 402 行全读 + 接口契约字典 §6）

- check_budget_alerts 已入本模块（budget.py，原寄生 web_api.main；随迁 notify 化——预算预警进站内铃铛）
