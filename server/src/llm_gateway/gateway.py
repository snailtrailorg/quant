"""LLM 网关 —— 平台所有 AI 调用的唯一入口。

国内模型 DeepSeek(主) + GLM(备)，路由+容灾+工具白名单。
"""

from __future__ import annotations
import os
import time
import logging
from pathlib import Path
from typing import Any, Callable, Literal, AsyncGenerator
from dataclasses import dataclass, field

import yaml
from openai import OpenAI, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("llm_gateway")

Tier = Literal["regular", "complex", "embedding"]
Role = Literal["viewer", "operator", "admin"]
Lang = Literal["zh", "en"]


@dataclass
class Tool:
    """Function calling 工具定义。"""
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str] | None = None
    tier: Literal["read", "operational"] = "read"


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] | None = None
    usage: dict = field(default_factory=dict)
    raw: Any = None


# ——— 工具白名单（三层） ———

# 读类（直接执行，Web+飞书）
READ_TOOLS = [
    Tool(name="query_position",     description="查当前持仓",
         input_schema={"type": "object", "properties": {}, "required": []}),
    Tool(name="query_pnl",          description="查盈亏",
         input_schema={"type": "object", "properties": {"from": {"type": "string"}}, "required": []}),
    Tool(name="query_strategy_status", description="查策略运行状态",
         input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": []}),
    Tool(name="query_risk_state",   description="查风控状态",
         input_schema={"type": "object", "properties": {}, "required": []}),
    Tool(name="get_astock_analysis", description="查 A 股研判结论",
         input_schema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": []}),
]

# 操作类（仅飞书，需确认）
OPERATIONAL_TOOLS = [
    Tool(name="emergency_halt",    description="一键熔断，停止所有自动开仓",
         input_schema={"type": "object", "properties": {"reason": {"type": "string"}}, "required": []},
         tier="operational"),
    Tool(name="risk_resume",       description="恢复交易（仅 Admin）",
         input_schema={"type": "object", "properties": {}, "required": []},
         tier="operational"),
    Tool(name="strategy_stop",     description="停某策略",
         input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
         tier="operational"),
    Tool(name="strategy_start",    description="启某策略",
         input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
         tier="operational"),
]

# 永不注册（高危参数修改类；下单类 place_order/cancel_order 已放开，走三级开关+风控 check_order）
FORBIDDEN_TOOLS = {"modify_risk_rule", "modify_strategy_params"}


# ——— LLM 网关核心 ———

class LLMGateway:
    """LLM 网关。"""

    def __init__(self, config_path: str | Path | None = None):
        self._models = self._load_models_from_db()  # {tier: [row,...]} 按 priority 排序
        self._failover = self._load_failover_config(config_path)  # failover 策略保留 config.yaml
        self._failed_counts: dict[str, int] = {}
        self._last_fail_time: dict[str, float] = {}

    def _load_models_from_db(self) -> dict[str, list[dict]]:
        """从 DB 读 enabled 模型（按 tier+priority），API key 解密。"""
        from src.data_platform.db import get_conn
        from src.web_api.crypto_utils import decrypt
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT id, name, provider, model, api_key_encrypted, base_url, "
                    "context_window, supports_tools, max_tokens, temperature, tier, priority "
                    "FROM llm_model_config WHERE enabled=true ORDER BY tier, priority"
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"DB 读 LLM 模型失败: {e}")
            return {}
        models: dict[str, list[dict]] = {}
        for r in rows:
            row = {
                "id": r[0], "name": r[1], "provider": r[2], "model": r[3],
                "api_key": decrypt(r[4]) if r[4] else "",
                "base_url": r[5], "context_window": r[6], "supports_tools": r[7],
                "max_tokens": r[8], "temperature": r[9], "tier": r[10], "priority": r[11],
            }
            models.setdefault(row["tier"], []).append(row)
        return models

    def reload_models(self) -> None:
        """Web 改配置后刷新缓存。"""
        self._models = self._load_models_from_db()

    # ── 路由逻辑 ──

    def _resolve_model(self, tier: Tier) -> tuple[dict, dict]:
        """从 DB 模型选 primary+fallback（同 tier 内 priority 小=优先）。"""
        rows = self._models.get(tier, []) or self._models.get("regular", [])
        if not rows:
            raise RuntimeError(f"无 enabled LLM 模型（tier={tier}），请 Admin 在 Web 配置")
        primary = rows[0]
        fallback = rows[1] if len(rows) > 1 else rows[0]
        return primary, fallback

    def _get_client(self, conf: dict) -> tuple[OpenAI, str]:
        """从 DB 模型行构建 OpenAI 客户端（api_key 已解密）。"""
        client = OpenAI(
            api_key=conf["api_key"],
            base_url=conf["base_url"],
            timeout=30,
        )
        return client, conf["model"]

    def _is_circuit_open(self, provider: str) -> bool:
        """熔断检查。"""
        cfg = self._failover.get("circuit_breaker", {})
        threshold = cfg.get("fail_threshold", 5)
        pause = cfg.get("pause_s", 300)
        n = self._failed_counts.get(provider, 0)
        if n < threshold:
            return False
        if time.time() - self._last_fail_time.get(provider, 0) > pause:
            self._failed_counts[provider] = 0
            return False
        return True

    def _record_fail(self, provider: str):
        self._failed_counts[provider] = self._failed_counts.get(provider, 0) + 1
        self._last_fail_time[provider] = time.time()

    # ── 工具过滤（按角色） ──

    def _filter_tools(self, role: Role, tools: list[Tool] | None) -> list[dict]:
        """按角色过滤可用工具 → OpenAI 格式。"""
        allowed = list(READ_TOOLS)  # 所有人都有读类
        if role in ("operator", "admin"):
            allowed += OPERATIONAL_TOOLS
        if tools:
            allowed = [t for t in tools if t.name not in FORBIDDEN_TOOLS]
        # 转 OpenAI 格式
        return [
            {"type": "function", "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            }}
            for t in allowed
        ]

    # ── 主接口 ──

    def chat(self, messages: list[dict], *,
             tier: Tier = "regular",
             tools: list[Tool] | None = None,
             role: Role = "viewer",
             lang: Lang | None = None,
             timeout: float = 30.0,
             retries: int = 1) -> LLMResponse:
        """同步聊天。"""
        openai_tools = self._filter_tools(role, tools)
        primary, fallback = self._resolve_model(tier)
        return self._do_chat(messages, primary, fallback, openai_tools, lang, timeout, retries)

    async def chat_stream(self, messages: list[dict], *,
                          tier: Tier = "regular",
                          tools: list[Tool] | None = None,
                          role: Role = "viewer",
                          lang: Lang | None = None,
                          ) -> AsyncGenerator[str, None]:
        """流式聊天。"""
        openai_tools = self._filter_tools(role, tools)
        primary, fallback = self._resolve_model(tier)
        async for chunk in self._do_chat_stream(messages, primary, fallback, openai_tools, lang):
            yield chunk

    # ── 内部执行 ──

    def _do_chat(self, messages: list[dict], primary: dict, fallback: dict,
                 openai_tools: list[dict], lang: str | None,
                 timeout: float, retries: int) -> LLMResponse:
        """实际调 LLM，带容灾。"""
        for attempt in range(max(retries, 1) + 1):
            for conf in [primary, fallback]:
                prov = conf["provider"]
                if self._is_circuit_open(prov):
                    logger.warning(f"熔断跳过: {prov}")
                    continue
                try:
                    client, model = self._get_client(conf)
                    msgs = self._inject_lang(messages, lang)
                    kwargs = dict(model=model, messages=msgs, timeout=timeout)
                    if openai_tools:
                        kwargs["tools"] = openai_tools
                        kwargs["tool_choice"] = "auto"
                    resp = client.chat.completions.create(**kwargs)
                    self._failed_counts[prov] = 0
                    return self._parse_response(resp)
                except Exception as e:
                    logger.warning(f"prov={prov} 失败: {e}")
                    self._record_fail(prov)
                    continue
            time.sleep(self._failover.get("retry_wait_s", 2))
        return LLMResponse(content="", usage={"error": "所有 provider 不可用"})

    async def _do_chat_stream(self, messages: list[dict], primary: dict, fallback: dict,
                              openai_tools: list[dict], lang: str | None) -> AsyncGenerator[str, None]:
        """流式调 LLM。"""
        for conf in [primary, fallback]:
            prov = conf["provider"]
            if self._is_circuit_open(prov):
                continue
            try:
                client = AsyncOpenAI(
                    api_key=conf["api_key"],
                    base_url=conf["base_url"],
                )
                msgs = self._inject_lang(messages, lang)
                kwargs = dict(model=conf["model"], messages=msgs, stream=True)
                if openai_tools:
                    kwargs["tools"] = openai_tools
                    kwargs["tool_choice"] = "auto"
                stream = await client.chat.completions.create(**kwargs)
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield delta.content
                self._failed_counts[prov] = 0
                return
            except Exception as e:
                logger.warning(f"stream prov={prov} 失败: {e}")
                self._record_fail(prov)
                continue
        yield "（所有 provider 不可用）"

    # ── 辅助 ──

    def _inject_lang(self, messages: list[dict], lang: str | None) -> list[dict]:
        """注入语言指令。"""
        if lang is None:
            import locale
            lang = "zh" if "zh" in locale.getdefaultlocale(("LANG", "en_US.UTF-8"))[0] else "en"
        if lang == "zh":
            instr = {"role": "system", "content": "请用中文回复。"}
        else:
            instr = {"role": "system", "content": "Please respond in English."}
        # 查是否已有 system 指令
        for m in messages:
            if m.get("role") == "system":
                m["content"] += f"\n\n[language: {lang}]"
                return messages
        return [instr] + messages

    def _parse_response(self, resp) -> LLMResponse:
        """解析 OpenAI 响应。"""
        choice = resp.choices[0]
        content = choice.message.content or ""
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                })
        return LLMResponse(
            content=content,
            tool_calls=tool_calls or None,
            usage={
                "input_tokens": getattr(resp.usage, "prompt_tokens", getattr(resp.usage, "input_tokens", 0)),
                "output_tokens": getattr(resp.usage, "completion_tokens", getattr(resp.usage, "output_tokens", 0)),
                "model": resp.model,
            },
            raw=resp,
        )

    def _load_failover_config(self, config_path: str | Path | None) -> dict:
        """failover 策略保留 config.yaml（模型配置已 DB 化）。"""
        path = config_path or Path(__file__).parent / "config.yaml"
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f)
            return cfg.get("failover", {})
        except Exception:
            return {"retry_wait_s": 2, "circuit_breaker": {"fail_threshold": 5, "pause_s": 300}}


# 单例
gateway = LLMGateway()