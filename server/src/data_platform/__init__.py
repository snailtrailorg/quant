"""数据中台 —— 统一数据入口。

用法:
    from src.data_platform import platform
    platform.ensure_daily("600000.SH", "20260701", "20260710")
    bars = platform.get_bar("600000.SHSE", "1D", date(2026,7,1), date(2026,7,10))

批 9 内存治理（2026-09-08）：
1. platform/DataPlatform 惰性导出（PEP 562）——_platform.py 的模块级 pandas 不再
   随包 import 进入 beat/调度链（任何 `from src.data_platform.X import` 都会先执行
   本 __init__）。`from src.data_platform import platform` 既有用法透明；子模块直连
   （db/rate_limit/schema 等）不受影响。
2. 模块名 platform.py → _platform.py（用户裁定「改就彻底改」）：原「包属性 platform/
   子模块 platform/单例实例」三层同名是在占用 import 机制的保留地（import 子模块会
   自动 setattr 包属性），eager 时代靠固定顺序掩盖、lazy 化即暴露「投毒」绑定竞态
   （双盲审 A/B 同判 P1，tests/test_data_platform_lazy.py 守门）。改名后名字不撞，
   import 机制与本懒加载各干各的。需要平台单例一律走包级
   `from src.data_platform import platform`。
"""

from importlib import import_module

from .schema import Bar, to_vt_symbol, parse_vt_symbol, to_ts_code

_LAZY_ATTRS: dict[str, str] = {"platform": "._platform", "DataPlatform": "._platform"}


def __getattr__(name: str):
    if name in _LAZY_ATTRS:
        return getattr(import_module(_LAZY_ATTRS[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_LAZY_ATTRS))


__all__ = ["platform", "DataPlatform", "Bar", "to_vt_symbol", "parse_vt_symbol", "to_ts_code"]
