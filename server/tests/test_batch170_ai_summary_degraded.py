"""批170（issue report 2）：降级分区必须进 AI 摘要 prompt，禁止无数据却报「态势平稳」。"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from src.health_monitor import security_collector as sc


class _Resp:
    content = "本次扫描 1 个分区数据不可用，结论不完整。"


class _GW:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return _Resp()


def _run(degraded):
    gw = _GW()
    import src.llm_gateway.gateway as gwmod
    with patch.object(gwmod, "gateway", gw, create=True):
        sc._ai_summary(dt.date(2026, 11, 4), [], [], degraded)
    return gw.calls[0]


def test_degraded_zone_named_in_prompt():
    msgs = _run([{"zone": "components", "reason": "依赖审计数据不可用: dep rows 表读失败"}])
    user = msgs[-1]["content"]
    assert "分区" in user and "components" in user
    assert "dep row" in user
    assert "不完整" in user


def test_system_prompt_forbids_all_clear_when_degraded():
    msgs = _run([{"zone": "components", "reason": "x"}])
    system = msgs[0]["content"]
    assert "降级分区" in system
    assert "态势平稳" in system          # 出现在禁用语里
    assert "禁止" in system


def test_healthy_scan_says_so():
    msgs = _run([])
    assert "全部分区采集正常" in msgs[-1]["content"]
