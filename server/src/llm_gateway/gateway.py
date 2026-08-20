"""LLM 网关 -- 平台所有 AI 调用的唯一入口。

国内模型 DeepSeek(主) + GLM(备)，路由+容灾+工具白名单+用量日志。
按 priority 全局主备容灾（2026-08-07 移除 tier 分级 + lang 注入）。
熔断半开探测 + 指数退避（P1.9）；用量写 llm_usage（PG，架构 §8）。
"""

from __future__ import annotations
import os
import time
import threading
import logging
from pathlib import Path
from typing import Any, Literal, AsyncGenerator
from dataclasses import dataclass, field

import yaml
from openai import OpenAI, AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("llm_gateway")

Role = Literal["viewer", "analyst", "trader", "admin"]


@dataclass
class Tool:
    """Function calling 工具声明（网关只声明 schema，执行由上层，P3.13 移除 handler）。"""
    name: str
    description: str
    input_schema: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] | None = None
    usage: dict = field(default_factory=dict)
    raw: Any = None


# --- 工具白名单（分层，按 RBAC 角色） ---

# 读类（所有人，直接执行，Web+飞书）
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

# 交易操作类（trader+admin，需确认卡片）：halt + 启停策略（resume 仅 Admin，见 ADMIN_TOOLS）
TRADER_TOOLS = [
    Tool(name="emergency_halt",    description="一键熔断，停止所有自动开仓",
         input_schema={"type": "object", "properties": {"reason": {"type": "string"}}, "required": []}),
    Tool(name="strategy_stop",     description="停某策略",
         input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}),
    Tool(name="strategy_start",    description="启某策略",
         input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}),
]

# Admin 专属（resume 仅 Admin）
ADMIN_TOOLS = [
    Tool(name="risk_resume",       description="恢复交易（仅 Admin）",
         input_schema={"type": "object", "properties": {}, "required": []}),
]

# 永不注册（高危参数修改类；下单类 place_order/cancel_order 已放开，走三级开关+风控 check_order）
FORBIDDEN_TOOLS = {"modify_risk_rule", "modify_strategy_params"}

# 操作类合集（TRADER+ADMIN），供外部判断"需确认卡片"的工具
OPERATIONAL_TOOLS = TRADER_TOOLS + ADMIN_TOOLS


# --- LLM 网关核心 ---

class LLMGateway:
    """LLM 网关。"""

    def __init__(self, config_path: str | Path | None = None):
        self._models = self._load_models_from_db()  # list[dict] 按 priority 排序
        if not self._models:
            logger.warning("LLM 网关初始化：无 enabled 模型")
        self._failover = self._load_failover_config(config_path)
        self._failed_counts: dict[str, int] = {}
        self._last_fail_time: dict[str, float] = {}
        self._half_open: dict[str, bool] = {}  # 半开状态标记（P1.9）
        self._lock = threading.Lock()  # 熔断状态并发安全（单例+线程池）

    def _load_models_from_db(self) -> list[dict]:
        """从 DB 读 enabled 模型（按 priority 全局排序），API key 解密。"""
        from src.data_platform.db import get_conn
        from src.quant_common.crypto import decrypt
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT id, name, provider, model, api_key_encrypted, base_url, "
                    "context_window, supports_tools, max_input_tokens, max_output_tokens, temperature, priority "
                    "FROM llm_model_config WHERE enabled=true ORDER BY priority"
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"DB 读 LLM 模型失败: {e}")
            return []
        models = []
        for r in rows:
            models.append({
                "id": r[0], "name": r[1], "provider": r[2], "model": r[3],
                "api_key": decrypt(r[4]) if r[4] else "",
                "base_url": r[5], "context_window": r[6], "supports_tools": r[7],
                "max_input_tokens": r[8], "max_output_tokens": r[9], "temperature": r[10], "priority": r[11],
            })
        return models

    def reload_models(self) -> None:
        """Web 改配置后刷新缓存。"""
        self._models = self._load_models_from_db()

    # ── 路由逻辑 ──

    def _get_primary_fallback(self) -> tuple[dict, dict]:
        """按 priority 全局排序取 primary + fallback。"""
        if not self._models:
            raise RuntimeError("无 enabled LLM 模型，请 Admin 在 Web 配置")
        primary = self._models[0]
        fallback = self._models[1] if len(self._models) > 1 else self._models[0]
        return primary, fallback

    # ── 输入 token 限制（程序控制） ──

    def _check_input_chars(self, messages: list[dict]) -> None:
        """入口字符过滤：超 50 万字符直接拒（防恶意长输入）。"""
        total = sum(len(m.get("content", "") or "") for m in messages)
        if total > 500_000:
            raise ValueError(f"输入字符数 {total} 超过 50 万上限")

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """估算输入 token（字符数 ×1.5，中文保守估，无依赖）。"""
        total_chars = sum(len(m.get("content", "") or "") for m in messages)
        return int(total_chars * 1.5)

    def _truncate_messages(self, messages: list[dict], max_input: int | None) -> list[dict]:
        """超 max_input_tokens 截断：保留 system + 最新一条，删中间历史。"""
        if not max_input:
            return messages
        if self._estimate_tokens(messages) <= max_input:
            return messages
        logger.warning(f"输入 token 超限 {max_input}，截断保留 system+最新")
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        if not non_system:
            return messages
        latest = [non_system[-1]]
        middle = non_system[:-1]
        while middle and self._estimate_tokens(system_msgs + middle + latest) > max_input:
            middle.pop(0)
        if self._estimate_tokens(system_msgs + middle + latest) > max_input:
            c = latest[0].get("content", "") or ""
            latest[0] = {**latest[0], "content": c[: max_input * 2 // 3]}
        return system_msgs + middle + latest

    def _get_client(self, conf: dict) -> tuple[OpenAI, str]:
        """构建 OpenAI 客户端（api_key 已解密）。"""
        client = OpenAI(
            api_key=conf["api_key"],
            base_url=conf["base_url"],
            timeout=30,
        )
        return client, conf["model"]

    def _is_circuit_open(self, provider: str) -> bool:
        """熔断检查（closed/open/half_open，P1.9 半开探测）。"""
        cfg = self._failover.get("circuit_breaker", {})
        threshold = cfg.get("fail_threshold", 5)
        pause = cfg.get("pause_s", 300)
        with self._lock:
            n = self._failed_counts.get(provider, 0)
            if n < threshold:
                return False  # closed
            # n >= threshold：open 或 half_open
            if self._half_open.get(provider):
                return True  # 半开中已有试探请求在跑，其他跳过
            if time.time() - self._last_fail_time.get(provider, 0) > pause:
                self._half_open[provider] = True  # 放一个试探请求
                return False
            return True  # open，跳过

    def _record_fail(self, provider: str):
        """记录失败（试探失败或正常失败 -> 回 open）。"""
        with self._lock:
            self._failed_counts[provider] = self._failed_counts.get(provider, 0) + 1
            self._last_fail_time[provider] = time.time()
            self._half_open[provider] = False  # 回 open

    def _reset_fail(self, provider: str):
        """试探成功 -> closed（全开）。"""
        with self._lock:
            self._failed_counts[provider] = 0
            self._half_open[provider] = False

    # ── 工具过滤（按角色；传入 tools 取交集，不能越权） ──

    def _filter_tools(self, role: Role, tools: list[Tool] | None) -> list[dict]:
        """按角色过滤可用工具 -> OpenAI 格式。

        角色白名单：viewer/analyst=读类；trader=读+halt+启停策略；admin=+resume。
        传入 tools 时与角色白名单取交集（调用方只能缩小范围，不能越权）。
        """
        allowed = list(READ_TOOLS)
        if role in ("trader", "admin"):
            allowed += TRADER_TOOLS
        if role == "admin":
            allowed += ADMIN_TOOLS
        allowed = [t for t in allowed if t.name not in FORBIDDEN_TOOLS]
        # None=角色默认白名单；[]=显式无工具（三档 analyze 踩到：空列表意图被无视
        # → LLM 看到工具集自发请求"查询更多信息"，非循环 chat 直接吐过渡语）
        if tools is not None:
            req_names = {t.name for t in tools}
            allowed = [t for t in allowed if t.name in req_names]
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
             tools: list[Tool] | None = None,
             role: Role = "viewer",
             timeout: float = 30.0,
             retries: int = 1,
             caller: str | None = None) -> LLMResponse:
        """同步聊天。caller=调用方标识（写 llm_usage，如 feishu/web_chat/daily_report）。"""
        self._check_input_chars(messages)
        openai_tools = self._filter_tools(role, tools)
        primary, fallback = self._get_primary_fallback()
        messages = self._truncate_messages(messages, primary.get("max_input_tokens"))
        return self._do_chat(messages, primary, fallback, openai_tools, timeout, retries, caller)

    async def chat_stream(self, messages: list[dict], *,
                          tools: list[Tool] | None = None,
                          role: Role = "viewer",
                          caller: str | None = None,
                          ) -> AsyncGenerator[str, None]:
        """流式聊天。"""
        self._check_input_chars(messages)
        openai_tools = self._filter_tools(role, tools)
        primary, fallback = self._get_primary_fallback()
        messages = self._truncate_messages(messages, primary.get("max_input_tokens"))
        async for chunk in self._do_chat_stream(messages, primary, fallback, openai_tools, caller):
            yield chunk

    # ── 内部执行 ──

    def _do_chat(self, messages: list[dict], primary: dict, fallback: dict,
                 openai_tools: list[dict],
                 timeout: float, retries: int, caller: str | None) -> LLMResponse:
        """实际调 LLM，带容灾（指数退避 + 半开熔断 + 用量日志）。"""
        max_attempts = max(retries, 1)
        for attempt in range(max_attempts + 1):
            for conf in [primary, fallback]:
                prov = conf["provider"]
                if self._is_circuit_open(prov):
                    logger.warning(f"熔断跳过: {prov}")
                    continue
                t0 = time.time()
                try:
                    client, model = self._get_client(conf)
                    kwargs = dict(model=model, messages=messages, timeout=timeout)
                    if openai_tools:
                        kwargs["tools"] = openai_tools
                        kwargs["tool_choice"] = "auto"
                    if conf.get("max_output_tokens"):
                        kwargs["max_tokens"] = conf["max_output_tokens"]
                    resp = client.chat.completions.create(**kwargs)
                    latency = int((time.time() - t0) * 1000)
                    self._reset_fail(prov)
                    parsed = self._parse_response(resp)
                    self._log_usage(prov, model, parsed.usage.get("input_tokens", 0),
                                    parsed.usage.get("output_tokens", 0), latency,
                                    success=True, caller=caller)
                    return parsed
                except Exception as e:
                    latency = int((time.time() - t0) * 1000)
                    logger.warning(f"prov={prov} 失败: {e}")
                    self._record_fail(prov)
                    self._log_usage(prov, conf["model"], 0, 0, latency,
                                    success=False, error_type=type(e).__name__, caller=caller)
                    continue
            # 指数退避（最后一次不睡）
            if attempt < max_attempts:
                time.sleep(self._failover.get("retry_wait_s", 2) * (2 ** attempt))
        return LLMResponse(content="", usage={"error": "所有 provider 不可用"})

    async def _do_chat_stream(self, messages: list[dict], primary: dict, fallback: dict,
                              openai_tools: list[dict], caller: str | None) -> AsyncGenerator[str, None]:
        """流式调 LLM。"""
        for conf in [primary, fallback]:
            prov = conf["provider"]
            if self._is_circuit_open(prov):
                continue
            t0 = time.time()
            try:
                client = AsyncOpenAI(
                    api_key=conf["api_key"],
                    base_url=conf["base_url"],
                )
                kwargs = dict(model=conf["model"], messages=messages, stream=True)
                # P2 修复（2026-08-20 双盲审计 A-D 组）：include_usage 让末 chunk 带 token 用量
                # ——原流式恒记 0，ws_chat 全部 token 不入 llm_usage（预算检查系统性低估）
                kwargs["stream_options"] = {"include_usage": True}
                if openai_tools:
                    kwargs["tools"] = openai_tools
                    kwargs["tool_choice"] = "auto"
                if conf.get("max_output_tokens"):
                    kwargs["max_tokens"] = conf["max_output_tokens"]
                stream = await client.chat.completions.create(**kwargs)
                usage_in = usage_out = 0
                async for chunk in stream:
                    if getattr(chunk, "usage", None):   # include_usage 的末 chunk
                        usage_in = getattr(chunk.usage, "prompt_tokens", 0) or 0
                        usage_out = getattr(chunk.usage, "completion_tokens", 0) or 0
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield delta.content
                latency = int((time.time() - t0) * 1000)
                self._reset_fail(prov)
                self._log_usage(prov, conf["model"], usage_in, usage_out, latency,
                                success=True, caller=caller)
                return
            except Exception as e:
                latency = int((time.time() - t0) * 1000)
                logger.warning(f"stream prov={prov} 失败: {e}")
                self._record_fail(prov)
                self._log_usage(prov, conf["model"], 0, 0, latency,
                                success=False, error_type=type(e).__name__, caller=caller)
                continue
        yield "（所有 provider 不可用）"

    # ── 辅助 ──

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

    def _log_usage(self, provider: str, model: str, input_tokens: int, output_tokens: int,
                   latency_ms: int, success: bool, error_type: str | None = None,
                   caller: str | None = None) -> None:
        """写 llm_usage（PG），架构 §8 用量日志。失败不影响主流程。"""
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute("SELECT 1 FROM llm_usage LIMIT 1")
                conn.execute(
                    "INSERT INTO llm_usage (provider, model, input_tokens, output_tokens, "
                    "latency_ms, success, error_type, caller) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (provider, model, input_tokens, output_tokens, latency_ms, success, error_type, caller))
                conn.commit()
        except Exception as e:
            logger.warning(f"llm_usage 写失败: {e}")

    def _load_failover_config(self, config_path: str | Path | None) -> dict:
        """failover 策略读 config.yaml（模型配置已 DB 化，tiers/providers 段已删）。"""
        path = config_path or Path(__file__).parent / "config.yaml"
        try:
            with open(path, encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
            return cfg.get("failover", {})
        except Exception:
            return {"retry_wait_s": 2, "circuit_breaker": {"fail_threshold": 5, "pause_s": 300}}


# 单例
gateway = LLMGateway()
