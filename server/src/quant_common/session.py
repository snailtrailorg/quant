"""A 股交易时段与 staleness 基线工具（从 strategy_runner.main 归位，2026-08-19）。

历史：曾寄生在应用入口导致 md_hub 模块级 import runner（连带 vnpy 链）+
health_monitor 被迫养 _in_session 复制体——归位后三者共用此份。
"""
from __future__ import annotations
import datetime as _dt


def in_astock_session(now=None) -> bool:
    """A 股交易时段（周一~周五 9:31-11:30 / 13:01-15:00）。

    节假日不感知——调用方必须叠加"今日已收到过 tick"条件，防止假日误判断流。
    """
    now = now or _dt.datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (931 <= hm <= 1130) or (1301 <= hm <= 1500)


def session_edge(cur: bool, was: bool) -> bool:
    """交易时段进入沿（False→True）。staleness 基线在沿上清零：跨日/午休/竞价窗口
    都不继承旧基线（S6 修订；三处循环共用，勿内联各写一份）。"""
    return cur and not was
