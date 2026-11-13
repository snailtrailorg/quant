"""批 175 L0 守门：canonical 单点（零行为变更）。"""
import ast
import os
from pathlib import Path

import pytest

from src.quant_common import symbology as S

_SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.mark.parametrize("inp,out", [
    ("600036.SHSE", "600036.SHSE"), ("600036.SSE", "600036.SHSE"),
    ("600036.XSHG", "600036.SHSE"), ("600036.SH", "600036.SHSE"),
    ("000001.SZSE", "000001.SZSE"), ("000001.XSHE", "000001.SZSE"), ("000001.SZ", "000001.SZSE"),
    ("830799.BSE", "830799.BSE"), ("830799.BJSE", "830799.BSE"), ("830799.BJ", "830799.BSE"),
    ("BTCUSDT.BINANCE", "BTCUSDT.BINANCE"), ("BTCUSDT.OKX", "BTCUSDT.OKX"),
    ("600036.shse", "600036.SHSE"),
])
def test_canonical(inp, out):
    assert S.canonical(inp) == out
    assert S.canonical(S.canonical(inp)) == out          # 幂等


@pytest.mark.parametrize("bad", ["600036", "600036.", ".SHSE", "", "600036.UNKNOWN"])
def test_canonical_rejects(bad):
    with pytest.raises(ValueError):
        S.canonical(bad)


@pytest.mark.parametrize("inp,mic", [
    ("600036.SHSE", "XSHG"), ("000001.SZSE", "XSHE"), ("830799.BSE", "BJSE"),
    ("600036.SSE", "XSHG"),
    ("BTCUSDT.BINANCE", None), ("BTCUSDT.OKX", None), ("600036", None), ("x.UNKNOWN", None),
])
def test_to_mic(inp, mic):
    assert S.to_mic(inp) == mic


@pytest.mark.parametrize("inp", ["600036.SHSE", "600036.SSE", "600036.SH", "600036.XSHG"])
def test_aliases_bidirectional_shanghai(inp):
    assert S.aliases(inp) == {"600036.SHSE", "600036.SSE"}


def test_aliases_shenzhen_and_crypto_single():
    assert S.aliases("000001.SZSE") == {"000001.SZSE"}
    assert S.aliases("BTCUSDT.BINANCE") == {"BTCUSDT.BINANCE"}


def test_mic_tables_consistent():
    assert set(S.VT_TO_MIC) == {"SHSE", "SZSE", "BSE"}
    assert all(S.MIC_TO_VT[m] == vt for vt, m in S.VT_TO_MIC.items())
    assert S.VT_TO_MIC["BSE"] == "BJSE"                   # 官方: XNEE=新西兰, XBSE=罗马尼亚
    for vt in S.VT_TO_MIC:
        assert S.to_mic(f"600000.{vt}") == S.VT_TO_MIC[vt]


# ——— 基线棘轮：全仓 "SSE"→"SHSE" dict 字面量只减不增（堵第五份映射）———
_BASELINE_COUNT = 2          # schema.TS_EXCHANGE_MAP 1 处 + strategy._VT_EXCHANGE_ALIASES 1 处；只降不升


def _find_sse_alias_dicts() -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for p in _SRC.rglob("*.py"):
        rel = str(p.relative_to(_SRC))
        if rel == "quant_common/symbology.py":
            continue      # 唯一合法映射表（其余处一律视为漂移）
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            pairs = list(zip(node.keys, node.values))
            for k, v in pairs:
                if (isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
                        and str(k.value).upper() == "SSE" and str(v.value).upper() == "SHSE"):
                    out.add((rel, node.lineno))
    return out


def test_sse_alias_literal_baseline_ratchet():
    actual = _find_sse_alias_dicts()
    assert len(actual) <= _BASELINE_COUNT, f"新增了 SSE→SHSE 字面量映射（应加 symbology）: {sorted(actual)}"
    assert len(actual) == _BASELINE_COUNT, f"基线条数不符（已收口则同步下调 _BASELINE_COUNT）: {sorted(actual)}"
