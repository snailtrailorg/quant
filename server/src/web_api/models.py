"""Web 后端 · Pydantic 请求体模型（从 main.py 迁出，零语义改动）。"""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator


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
    nickname: str = ""          # 批11：邀请开通四字段（选填）
    avatar: str = ""            # 批11：头像 dataURL base64（选填，注册成功后落盘）
    lang: str = "en"

class ForgotReq(BaseModel):
    email: str
    lang: str = "en"


class EmailChangeReq(BaseModel):   # 批20：验证成功才改（无中间态）
    new_email: str
    current_password: str
    lang: str = "en"


class EmailConfirmReq(BaseModel):
    token: str


class PhoneCodeReq(BaseModel):   # 批30：手机号验证码修改（码绑死手机——存侧 {code,phone}，change 不收 body 手机号）
    phone: str
    current_password: str


class PhoneChangeReq(BaseModel):
    code: str

class ResetReq(BaseModel):
    token: str
    new_password: str

class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str

class ChatReq(BaseModel):
    message: str

class LLMModelReq(BaseModel):
    name: str
    provider: str
    model: str
    api_key: str = ""
    base_url: str
    context_window: int = Field(32768, ge=0, le=2_000_000)      # 批36b-α：模型层值域（0=用模型缺省）
    supports_tools: bool = True
    max_input_tokens: int | None = Field(None, ge=0, le=2_000_000)
    max_output_tokens: int | None = Field(None, ge=0, le=2_000_000)
    temperature: float | None = Field(None, ge=0, le=2)
    priority: int = Field(10, ge=1, le=100)
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
    user_id: int | None = None   # 批11C：绑定平台账号（管理面通道——平台级 bot 的绑定恢复路径，A-P0-1 修）

class LlmBudgetReq(BaseModel):
    provider: str | None = None
    daily_token_limit: int | None = Field(None, ge=0)            # 0/负=禁用告警（budget.py falsy 跳过）——写侧仍拒负
    monthly_cost_limit: float | None = Field(None, ge=0)
    alert_threshold_pct: int = Field(80, ge=1, le=100)
    enabled: bool = True

class DataSourceReq(BaseModel):
    provider: str
    name: str
    credentials: str = ""
    params: str | None = None
    usage_limit: int | None = Field(None, ge=0)                  # 批36b-α（0 语义=无限制——UI placeholder 表达）
    enabled: bool = True

class RateLimitOverrideReq(BaseModel):
    """单参数限速覆写（L2）或熔断参数写入（params.circuit_breaker）。

    - api_name+value 非空：覆写（value=null 删除覆写回落预设）
    - circuit_breaker 非空：写熔断参数 {"fail_threshold": int, "reset_timeout": float}
    """
    api_name: str | None = None
    value: float | None = None
    circuit_breaker: dict | None = None

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
    initial_capital: float = Field(1_000_000, gt=0, le=1e10)
    leverage: int = Field(1, ge=1, le=100)