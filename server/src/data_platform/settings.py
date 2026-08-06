"""集中读取环境变量配置（settings 单例）。

各模块统一从这里取 env，避免散落的 os.environ.get + load_dotenv。
现有 db.py / risk.py / scheduler 等 暂保留各自 load_dotenv（兼容），新配置走这里。
"""
import os
from dotenv import load_dotenv

load_dotenv()


def is_live_trading_enabled() -> bool:
    """实盘交易总开关（第一级，.env ENABLE_LIVE_TRADING）。

    生产默认 false，测试环境 true。
    需配合 Web 分项开关（live_trading_config 表）+ 策略 enabled 共同开启（三级 AND）。
    """
    return os.environ.get("ENABLE_LIVE_TRADING", "false").lower() in ("true", "1", "yes")
