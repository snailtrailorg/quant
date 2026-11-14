"""标的主标识归一（批175 L0 · canonical 单点，层 0 底座）。

外部异构后缀只允许活在 adapter 边界（tushare `.SH`、聚宽 `.XSHG`、vnpy `.SSE`），
进入平台统一为 vt（`.SHSE/.SZSE/.BSE`）。本模块是**唯一入站别名表**——新增映射只能加
`_VENDOR_ALIASES`；基线棘轮（`tests/test_symbology.py`）锁全仓 "SSE→SHSE" 字面量只减不增。

设计要点（见 `flow/方案/标的主标识体系-讨论-20261113.md` §七）：
- `canonical` 幂等；**裸码/未知后缀一律 ValueError（禁猜）**——猜错=拉错标的的数据，比失败更糟。
- `aliases` **双向**（SHSE↔SSE）：过渡期 `position_symbol_candidates` 仍需要（否则现网
  `position_snapshot` 还是 `.SSE` 时 SELL 被截 0，重演 171n）。
- crypto（`.BINANCE/.OKX`）直通、`to_mic` 返回 None（ISO 10383 不覆盖加密交易所）。
"""
from __future__ import annotations

# vt → ISO 10383 MIC（venue 标识，**不是证券主键**；官方值见 docs/reference/iso/ISO10383_MIC.csv）
VT_TO_MIC: dict[str, str] = {"SHSE": "XSHG", "SZSE": "XSHE", "BSE": "BJSE"}
MIC_TO_VT: dict[str, str] = {v: k for k, v in VT_TO_MIC.items()}

# 各源/运行时后缀 → vt（**唯一入站别名表**；含 vnpy 的 SSE）
_VENDOR_ALIASES: dict[str, str] = {
    "SHSE": "SHSE", "SSE": "SHSE", "XSHG": "SHSE", "SH": "SHSE",
    "SZSE": "SZSE", "XSHE": "SZSE", "SZ": "SZSE",
    "BSE": "BSE", "BJSE": "BSE", "BJ": "BSE",
    "BINANCE": "BINANCE", "OKX": "OKX",   # crypto 直通（无 MIC）
}

# 双向别名（只沪市有两套写法；深市两端恰好都是 SZSE）
_BIDIRECTIONAL: dict[str, tuple[str, ...]] = {
    "SHSE": ("SHSE", "SSE"), "SSE": ("SHSE", "SSE"),
}

# tushare 的 `SZE`（SZSE 变体）——对齐 schema.TS_EXCHANGE_MAP 历史键
_VENDOR_ALIASES.setdefault("SZE", "SZSE")

# 公开**只读**视图（供 schema 等派生；写者会在运行时 TypeError，而非污染 L0 唯一表）
from types import MappingProxyType as _MPT
VENDOR_ALIASES = _MPT(_VENDOR_ALIASES)

_CRYPTO_VT = ("BINANCE", "OKX")


def split_raw(symbol: str) -> tuple[str, str]:
    """`'600036.SHSE' -> ('600036', 'SHSE')`；无后缀/空段 → ValueError（禁猜）。"""
    if not isinstance(symbol, str) or "." not in symbol:
        raise ValueError(f"symbol 缺交易所后缀（禁猜）: {symbol!r}")
    raw, ex = symbol.rsplit(".", 1)
    if not raw or not ex:
        raise ValueError(f"symbol 形态非法: {symbol!r}")
    return raw, ex.upper()


def canonical(symbol: str) -> str:
    """任意已知形态 → vt（幂等）。未知后缀/裸码 → ValueError。"""
    raw, ex = split_raw(symbol)
    vt = _VENDOR_ALIASES.get(ex)
    if vt is None:
        raise ValueError(
            f"未知交易所后缀 {ex!r}（symbol={symbol!r}）；新映射须加 symbology._VENDOR_ALIASES")
    return f"{raw}.{vt}"


def to_mic(symbol: str) -> str | None:
    """vt/任意已知形态 → ISO MIC；crypto 或不可归一 → None。"""
    try:
        _, vt = split_raw(canonical(symbol))
    except ValueError:
        return None
    return VT_TO_MIC.get(vt)


def aliases(symbol: str) -> set[str]:
    """双向别名候选集（替代 `strategy._VT_EXCHANGE_ALIASES` 的查询语义）。

    裸码/无后缀返回 `{symbol}`（**不猜**、不抛）——调用方在 fail-closed 路径上
    （`_held_volume`/手动 SELL 截断），抛错会把"截断到 0"变成"按原量提交"（fail-open）。
    """
    try:
        raw, ex = split_raw(symbol)
    except ValueError:
        return {symbol}
    vt = _VENDOR_ALIASES.get(ex.upper(), ex.upper())
    out = {f"{raw}.{vt}"}
    for alt in _BIDIRECTIONAL.get(vt, ()):
        out.add(f"{raw}.{alt}")
    return out
