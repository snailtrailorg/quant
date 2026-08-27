"""Web 后端 · Pydantic 请求体模型（从 main.py 迁出，零语义改动）。"""
from __future__ import annotations
from pydantic import BaseModel, field_validator


class LoginReq(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "viewer"

class StrategyConfig(BaseModel):
    id: str
    name: str
    type: str
    symbol: str
    adapter: str
    enabled: bool = True
    factors: list = []
    aggregator: dict = {}
    risk: dict = {}
    params: dict = {}

class InviteReq(BaseModel):
    email: str
    lang: str = "en"

class RegisterReq(BaseModel):
    token: str
    username: str
    password: str
    lang: str = "en"

class ForgotReq(BaseModel):
    email: str
    lang: str = "en"

class ResetReq(BaseModel):
    token: str
    new_password: str

class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str

class LogAnalyzeReq(BaseModel):
    logs: list[dict] | None = None
    task_id: str | None = None

class ChatReq(BaseModel):
    message: str

class LLMModelReq(BaseModel):
    name: str
    provider: str
    model: str
    api_key: str = ""
    base_url: str
    context_window: int = 32768
    supports_tools: bool = True
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    priority: int = 10
    enabled: bool = False

    @field_validator("provider")
    @classmethod
    def _provider_whitelist(cls, v: str) -> str:
        allowed = {"deepseek", "glm", "zhipu", "qwen", "custom"}
        v = (v or "").strip().lower()
        if v not in allowed:
            raise ValueError(f"provider 需为 {sorted(allowed)} 之一（运行期国内模型铁律，接新供应商走代码评审）")
        return v

class IMBotCreateReq(BaseModel):
    provider: str
    name: str
    description: str = ""
    default_role: str = "viewer"
    credentials: dict = {}

class IMBotUpdateReq(BaseModel):
    name: str | None = None
    description: str | None = None
    default_role: str | None = None
    lang: str | None = None
    credentials: dict | None = None

class IMBotUserReq(BaseModel):
    im_user_id: str
    role: str

class LlmBudgetReq(BaseModel):
    provider: str | None = None
    daily_token_limit: int | None = None
    monthly_cost_limit: float | None = None
    alert_threshold_pct: int = 80
    enabled: bool = True

class DataSourceReq(BaseModel):
    provider: str
    name: str
    credentials: str = ""
    params: str | None = None
    usage_limit: int | None = None
    enabled: bool = True

class PointsTierReq(BaseModel):
    """积分档切换（四层限流 L1）：tier 必须在 POINTS_PRESETS 键中（后端校验）。"""
    tier: int

class RateLimitOverrideReq(BaseModel):
    """单参数限速覆写（L2）或熔断参数写入（params.circuit_breaker）。

    - api_name+value 非空：覆写（value=null 删除覆写回落预设）
    - circuit_breaker 非空：写熔断参数 {"fail_threshold": int, "reset_timeout": float}
    """
    api_name: str | None = None
    value: float | None = None
    circuit_breaker: dict | None = None

class ChannelReq(BaseModel):
    provider: str
    name: str
    credentials: str = ""
    params: str | None = None
    enabled: bool = True

class BrokerReq(BaseModel):
    provider: str
    name: str
    credentials: str = ""
    params: str | None = None
    enabled: bool = True

class RiskRuleReq(BaseModel):
    name: str
    type: str
    params: str = "{}"
    enabled: bool = True

class PoolReq(BaseModel):
    id: str
    name: str
    category: str = "astock"
    symbolsStr: str = ""
    description: str = ""
    minute_history_start: str | None = None

class StrategyAccountReq(BaseModel):
    strategy_id: str
    account_id: str
    broker_provider: str = "xtp"
    initial_capital: float = 1000000
    leverage: int = 1