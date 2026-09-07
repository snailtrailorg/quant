"""数据中台 —— 统一数据入口。

用法:
    from src.data_platform import platform
    platform.ensure_daily("600000.SH", "20260701", "20260710")
    bars = platform.get_bar("600000.SHSE", "1D", date(2026,7,1), date(2026,7,10))

批 9 内存治理（2026-09-08）：platform/DataPlatform 惰性导出（PEP 562）——platform.py
的模块级 pandas 不再随包 import 进入 beat/调度链（任何 `from src.data_platform.X import`
都会先执行本 __init__）。`from src.data_platform import platform` 既有用法透明；
子模块直连（db/rate_limit/schema 等）不受影响。

**禁直接 `import src.data_platform.platform` / `from src.data_platform.platform import X`**
（双盲审 B-P1）：会先把包属性毒化成模块且绕过 __getattr__ 重绑。需要平台单例一律
走包级 `from src.data_platform import platform`。
"""

from importlib import import_module

from .schema import Bar, to_vt_symbol, parse_vt_symbol, to_ts_code

_LAZY_ATTRS: dict[str, str] = {"platform": ".platform", "DataPlatform": ".platform"}


def __getattr__(name: str):
    if name in _LAZY_ATTRS:
        mod = import_module(_LAZY_ATTRS[name], __name__)
        # 子模块 import 的副作用会把同名模块绑到包属性（pkg.platform=module）。
        # 解析任一懒名时**全量重绑**所有懒名到实例/类——只重绑被请求的名字挡不住
        # 「跨名投毒」（先 from ... import DataPlatform 再用 platform 仍拿到模块，
        # 双盲审 A-P1 实测）。既有 `from src.data_platform import platform` 拿到的
        # 一直是 DataPlatform() 单例（is_trading_day/ensure_daily 在其上）。
        for _n in _LAZY_ATTRS:
            globals()[_n] = getattr(mod, _n)
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_LAZY_ATTRS))


__all__ = ["platform", "DataPlatform", "Bar", "to_vt_symbol", "parse_vt_symbol", "to_ts_code"]
