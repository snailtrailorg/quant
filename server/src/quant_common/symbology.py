"""标的主标识归一（L0 单一映射层 · 批177 起 canonical = ISO 10383 MIC）。

外部异构后缀只允许活在 adapter 边界（tushare `.SH`、聚宽 `.XSHG`、vnpy `.SSE`、旧项目 `.SHSE`），
进入平台统一为 **MIC**：`.XSHG`（上交所）/ `.XSHE`（深交所）/ `.BJSE`（北交所）。
crypto 无 ISO MIC，用**自定义 venue 码**（已在官方表中确认为未占用）：`.XBIN`（币安）、`.XOKX`（欧易）。

本模块是**唯一入站别名表**——新增映射只能加 `_VENDOR_ALIASES`；
基线棘轮（`tests/test_symbology.py`）锁全仓入站 SSE 字面量，防第五份映射。

设计要点：
- `canonical` 幂等；**裸码/未知后缀一律 ValueError（禁猜）**——猜错=拉错标的的数据，比失败更糟。
- `aliases` **双向**：历史行（`order_log`/`bar_*` 迁移前）仍可能是旧 vt 写法，查询侧需多写法候选。
- `to_mic` 对 crypto 返回 None（ISO 10383 不覆盖加密交易所）。
"""
from __future__ import annotations

from types import MappingProxyType as _MPT

# venue 旧写法 → MIC（**唯一映射表**；crypto 用自定义码）
MIC_BY_VENUE: dict[str, str] = {
    "SHSE": "XSHG", "SZSE": "XSHE", "BSE": "BJSE",
    "BINANCE": "XBIN", "OKX": "XOKX",
}

# 各源/运行时后缀 → MIC（入站别名；含 vnpy 的 SSE、tushare 的 SH/SZ/BJ、旧项目 vt）
_VENDOR_ALIASES: dict[str, str] = {
    "XSHG": "XSHG", "SHSE": "XSHG", "SSE": "XSHG", "SH": "XSHG",
    "XSHE": "XSHE", "SZSE": "XSHE", "SZ": "XSHE",
    "BJSE": "BJSE", "BSE": "BJSE", "BJ": "BJSE",
    "XBIN": "XBIN", "BINANCE": "XBIN",
    "XOKX": "XOKX", "OKX": "XOKX",
}

# 同一 venue 的历史/运行时写法（查询侧候选集；迁移完成后仍保留以兼容历史行）
_ALIAS_FORMS: dict[str, tuple[str, ...]] = {
    "XSHG": ("XSHG", "SHSE", "SSE"),
    "XSHE": ("XSHE", "SZSE"),
    "BJSE": ("BJSE", "BSE"),
    "XBIN": ("XBIN", "BINANCE"),
    "XOKX": ("XOKX", "OKX"),
}

# 注意：tushare trade_cal 的交易所名 `SZE` **不是符号后缀**（批131 契约：`600000.SZE` 必须硬拒），
# 故不入本表——它只作 trade_cal 的 exchange 参数值使用，不参与 symbol 归一。

# 公开**只读**视图（供 schema 等派生；写者会在运行时 TypeError，而非污染 L0 唯一表）
VENDOR_ALIASES = _MPT(_VENDOR_ALIASES)

# 兼容旧名（批175 引入）：vt→MIC；新代码请直接用 MIC_BY_VENUE / vt_suffix
VT_TO_MIC = _MPT({k: v for k, v in MIC_BY_VENUE.items()})
MIC_TO_VT = _MPT({v: k for k, v in MIC_BY_VENUE.items()})

_CRYPTO_VENUES = ("XBIN", "XOKX")


def split_raw(symbol: str) -> tuple[str, str]:
    """`'600036.XSHG' -> ('600036', 'XSHG')`；无后缀/空段 → ValueError（禁猜）。"""
    if not isinstance(symbol, str) or "." not in symbol:
        raise ValueError(f"symbol 缺交易所后缀（禁猜）: {symbol!r}")
    raw, ex = symbol.rsplit(".", 1)
    if not raw or not ex:
        raise ValueError(f"symbol 形态非法: {symbol!r}")
    return raw, ex.upper()


def vt_suffix(alias: str) -> str:
    """**交易所维度**别名/后缀 → MIC（`SSE/SHSE/SH/XSHG → XSHG`；`BINANCE → XBIN`）。

    与 `canonical` 的分工：本函数只吃后缀（无标的代码），供"后缀→vnpy 枚举/hub 键"
    这类只需交易所的调用方使用——避免它们各自再写一份映射。
    """
    if not isinstance(alias, str) or not alias.strip():
        raise ValueError(f"交易所后缀为空: {alias!r}")
    ex = alias.strip().upper()
    mic = _VENDOR_ALIASES.get(ex)
    if mic is None:
        raise ValueError(f"未知交易所后缀 {ex!r}；新映射须加 symbology._VENDOR_ALIASES")
    return mic


def canonical(symbol: str) -> str:
    """任意已知形态 → MIC 形态（幂等）。未知后缀/裸码 → ValueError。"""
    raw, ex = split_raw(symbol)
    return f"{raw}.{vt_suffix(ex)}"


def to_mic(symbol: str) -> str | None:
    """规范形态 → ISO MIC；crypto（XBIN/XOKX）或不可归一 → None。"""
    try:
        _, mic = split_raw(canonical(symbol))
    except ValueError:
        return None
    return None if mic in _CRYPTO_VENUES else mic


def aliases(symbol: str) -> set[str]:
    """同标的的历史/运行时写法候选集（替代 `strategy._VT_EXCHANGE_ALIASES` 的查询语义）。

    裸码/无后缀返回 `{symbol}`（**不猜**、不抛）——调用方在 fail-closed 路径上
    （`_held_volume`/手动 SELL 截断），抛错会把"截断到 0"变成"按原量提交"（fail-open）。
    """
    try:
        raw, ex = split_raw(symbol)
    except ValueError:
        return {symbol}
    mic = _VENDOR_ALIASES.get(ex.upper(), ex.upper())
    return {f"{raw}.{form}" for form in _ALIAS_FORMS.get(mic, (mic,))}
