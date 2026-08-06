# 01 - LLM 网关

## 1. 目的

平台所有 AI 调用的**唯一入口**。把国内模型（DeepSeek / GLM）的路由、容灾、日志、用量统计、工具注册集中在一层，上层模块不直接接触具体模型厂商。

**为什么自建而非装外部代理**：
- DeepSeek 和 GLM 都提供 OpenAI 兼容接口，统一接口几乎零成本，网关只需做路由+容灾，约 150 行 Python。
- 资源紧：一个进程内模块，零额外服务。
- 可控：路由规则、容灾策略、密钥、用量都在手，且 function calling 工具注册在网关层统一管控。

## 2. 职责

1. **统一接口**：上层只调 `chat()`，屏蔽 DeepSeek / GLM 差异。
2. **路由**：任务档位（regular / complex / embedding）→ provider（primary + fallback）。
3. **容灾**：HTTP 5xx / 429 / 超时 / 配额空 → 自动切 fallback provider。
4. **工具注册（function calling）**：上层注册工具函数，LLM 可调用；**下单类工具永不注册**（AI 层权限隔离）。
5. **日志与用量**：每次调用记 provider / 模型 / tokens / 延迟 / 成败，供 Web 后台展示和成本分析。
6. **密钥管理**：从加密配置/环境变量读，不进代码。

## 3. 边界与非目标

- **不做**：模型微调、本地推理托管（要本地部署走单独的推理服务，网关仍可路由到它）。
- **不暴露**：不对外网开放，只供平台内部进程调用。
- **非目标**：不做计费分摊、多租户（那是 One-API 的事，个人平台用不上）。
- **上层**不 import DeepSeek/GLM SDK，只 import 网关。

## 4. 依赖

- `openai` Python SDK（OpenAI 兼容客户端，换 base_url 调 DeepSeek/GLM）
- `httpx`（超时/重试控制）
- 加密配置读取（复用平台配置层）
- Valkey（可选，缓存嵌入向量；用量统计写 PG）

## 5. 接口定义

### 5.1 主接口
```python
from typing import Callable, Literal

Tier = Literal["regular", "complex", "embedding"]

class Message(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str

class Tool:                         # function calling 工具
    name: str
    description: str
    input_schema: dict             # JSON Schema
    handler: Callable[[dict], str] # 客户端执行，结果回 LLM

class LLMResponse:
    content: str
    tool_calls: list[dict] | None
    usage: dict                    # {provider, model, input_tokens, output_tokens, latency_ms}
    raw: object

class LLMGateway:
    def chat(self, messages: list[Message], *,
             tier: Tier = "regular",
             tools: list[Tool] | None = None,
             role: str = "viewer",          # RBAC: 按角色过滤可用工具集
             lang: str | None = None,       # 语言: "zh"|"en"，自动注入系统 prompt；None=检测
             timeout: float = 30.0,
             retries: int = 1) -> LLMResponse: ...

    async def chat_stream(self, messages: list[Message], *,
                          tier: Tier = "regular", role: str = "viewer",
                          tools: list[Tool] | None = None,
                          lang: str | None = None
                          ) -> AsyncGenerator[StreamDelta, None]:
        """流式输出，底层用 DeepSeek/GLM stream=True。
        Web /ws/chat 流式返回、飞书长回复分块推送走此接口。"""

    def embed(self, texts: list[str], *, tier: Tier = "embedding") -> list[list[float]]: ...
```

### 5.2 路由配置（YAML，运行期可热重载）
```yaml
tiers:
  regular:
    primary: deepseek-v4-flash
    fallback: glm-5.2
  complex:
    primary: deepseek-v4-pro
    fallback: glm-5.2
  embedding:
    primary: bge-m3-worker         # bge-m3 跑在独立 worker 进程，不进交易热路径
    fallback: deepseek-embedding   # API embedding 在线兜底

providers:
  deepseek:
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    models:
      deepseek-v4-flash: { context: 128000, supports_tools: true }
      deepseek-v4-pro:   { context: 128000, supports_tools: true }
  glm:
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key_env: GLM_API_KEY
    models:
      glm-5.2: { context: 128000, supports_tools: true }

failover:
  triggers: [http_5xx, http_429, timeout, quota_exceeded]
  retry_wait_s: 2
  circuit_breaker: { fail_threshold: 5, pause_s: 300 }

usage:
  log: true                       # 每次 chat 写 PG
  store: postgres                # table: llm_usage
```

> 模型型号规格（V4 / V4-flash / V4-pro / GLM-5.2）以官方为准，上面 context/supports_tools 是占位，接入时按官方文档回填。

## 6. 容灾设计

1. 调用 primary，触发任一 failover 条件 → 切 fallback，记一次 fallback。
2. 重试策略：先在 provider 内重试 1 次（指数退避），仍失败切 provider。
3. 熔断：某 provider 连续失败 N 次（默认 5）→ 暂停 5 分钟，期间直接走 fallback，不试 primary。
4. 超时：`timeout` 默认 30s；可按 tier 覆盖（complex 任务给 120s）。
5. 降级：tier=complex 的 primary 和 fallback 都失败 → 降级用 regular 档试一次，仍失败则抛 `LLMUnavailable` 给上层，上层决定是否跳过该 AI 步骤。

## 7. 工具调用（function calling）与权限

工具白名单**分层**，在网关层强制：

| 层 | 工具 | 触发入口 | 确认 |
|---|---|---|---|
| 读类 | `query_position` / `query_pnl` / `query_strategy_status` / `get_astock_analysis` / `query_risk_state` / `query_orders` | Web chat、飞书 | 直接执行 |
| 操作类 | `emergency_halt` / `risk_resume` / `strategy_stop` / `strategy_start` | **仅飞书**（Web 不开放） | 交互卡片确认+超时失效 |
| 永不注册 | `place_order` / `cancel_order` / `modify_risk_rule` / `modify_strategy_params` | — | — |

- **下单类工具永不注册**——这是 AI 层权限隔离的硬保障，AI 不握方向盘。
- **操作类工具仅飞书通道可用**，且 LLM 调用时网关拦截 → 发确认卡片 → 用户确认才执行（详见 11）。
- Web chat（08）只开放读类，操作类走 Web 的按钮 API（`/api/risk/halt`），不经 LLM。
- **工具集按调用者角色 per-request 过滤**：`chat()` 调用方带 `role`（viewer/operator/admin），网关按角色拼出可用工具集再发给模型——viewer 只见读类，operator 见读+操作类，admin 见全部。这是 RBAC 在 AI 层的落点。
- 工具由网关在客户端执行（DeepSeek/GLM 都支持 OpenAI 风格 tool calling loop），结果回 LLM，循环到 LLM 不再调用工具。

## 8. 用量与日志

每次 `chat()` 成功或失败都写一条 `llm_usage`（PG 表）：

| 字段 | 说明 |
|---|---|
| ts | 时间戳 |
| tier / provider / model | 本次实际服务方 |
| input_tokens / output_tokens | 计费用 |
| latency_ms | 延迟 |
| success / error_type | 成败与失败类型 |
| caller | 调用方模块（astock / daily_report / web_chat / ...）|

Web 后台读这张表做成本和健康度看板。

## 9. 与其它模块交互

- **盘后报告 / 可转债条款解读 / 研报情绪**：调 `chat(tier=complex|regular)`。
- **Web 自然语言查询**：调 `chat(tools=[只读工具])`。
- **日志归因**：把异常日志喂 `chat(tier=regular)`，输出归因 → 告警。
- **数据中台 RAG**：调 `embed()`；向量存 pgvector。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 客户端 | `openai` SDK + 换 base_url | 两家都兼容，零额外抽象 |
| 进程内模块 vs 独立服务 | 进程内模块 | 资源紧，无额外服务；将来要多服务共享再拆 One-API |
| 嵌入模型 | bge-m3 独立 worker 进程 + 盘后批量 | bge-m3 ~560M/2.2GB/CPU 慢，不能进交易热路径；走独立进程隔离资源，盘后批量向量化。在线兜底用 API embedding。中文金融文本不降级到英文 MiniLM（质量不够）|
| 工具白名单 | 网关层强制 | AI 层权限隔离的硬保障 |
| 配置热重载 | YAML + 文件监听 | 改模型路由不重启平台 |
