"""批 175 L0 守门：canonical 单点（零行为变更）。"""
import ast
import os
from pathlib import Path

import pytest

from src.quant_common import symbology as S

_SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.mark.parametrize("inp,out", [
    ("600036.XSHG", "600036.XSHG"), ("600036.SSE", "600036.XSHG"),
    ("600036.XSHG", "600036.XSHG"), ("600036.SH", "600036.XSHG"),
    ("000001.XSHE", "000001.XSHE"), ("000001.XSHE", "000001.XSHE"), ("000001.SZ", "000001.XSHE"),
    ("830799.BJSE", "830799.BJSE"), ("830799.BJSE", "830799.BJSE"), ("830799.BJ", "830799.BJSE"),
    ("BTCUSDT.XBIN", "BTCUSDT.XBIN"), ("BTCUSDT.XOKX", "BTCUSDT.XOKX"),
    ("600036.shse", "600036.XSHG"),
])
def test_canonical(inp, out):
    assert S.canonical(inp) == out
    assert S.canonical(S.canonical(inp)) == out          # 幂等


@pytest.mark.parametrize("bad", ["600036", "600036.", ".XSHG", "", "600036.UNKNOWN"])
def test_canonical_rejects(bad):
    with pytest.raises(ValueError):
        S.canonical(bad)


@pytest.mark.parametrize("inp,mic", [
    ("600036.XSHG", "XSHG"), ("000001.XSHE", "XSHE"), ("830799.BJSE", "BJSE"),
    ("600036.SSE", "XSHG"),
    ("BTCUSDT.XBIN", None), ("BTCUSDT.XOKX", None), ("600036", None), ("x.UNKNOWN", None),
])
def test_to_mic(inp, mic):
    assert S.to_mic(inp) == mic


@pytest.mark.parametrize("alias,vt", [
    ("SSE", "SHSE"), ("SHSE", "SHSE"), ("sh", "SHSE"), ("XSHG", "SHSE"),
    ("SZSE", "SZSE"), ("XSHE", "SZSE"), ("BJ", "BSE"), ("BJSE", "BSE"),
    ("BINANCE", "BINANCE"), ("okx", "OKX"),
])
def test_vt_suffix(alias, vt):
    assert S.vt_suffix(alias) == vt


@pytest.mark.parametrize("bad", ["", "  ", None, "OF", "UNKNOWN", "SZE"])   # SZE=交易历交易所名，非 vt 后缀
def test_vt_suffix_rejects(bad):
    with pytest.raises(ValueError):
        S.vt_suffix(bad)


@pytest.mark.parametrize("inp", ["600036.XSHG", "600036.SSE", "600036.SH", "600036.XSHG"])
def test_aliases_bidirectional_shanghai(inp):
    assert S.aliases(inp) == {"600036.XSHG", "600036.SSE"}


def test_aliases_shenzhen_and_crypto_single():
    assert S.aliases("000001.XSHE") == {"000001.XSHE"}
    assert S.aliases("BTCUSDT.XBIN") == {"BTCUSDT.XBIN"}


def test_mic_tables_consistent():
    assert set(S.VT_TO_MIC) == {"SHSE", "SZSE", "BSE"}
    assert all(S.MIC_TO_VT[m] == vt for vt, m in S.VT_TO_MIC.items())
    assert S.VT_TO_MIC["BSE"] == "BJSE"                   # 官方: XNEE=新西兰, XBSE=罗马尼亚
    for vt in S.VT_TO_MIC:
        assert S.to_mic(f"600000.{vt}") == S.VT_TO_MIC[vt]


# ——— 入站别名棘轮：全仓**入站映射形态**的 "SSE" 常量必须为零（唯一表=symbology）———
#
# 批175e 扩形态：原实现只认 {"SSE":"SHSE"} 字符串值 dict，实测 8 种等价写法漏 6
# （dict() 构造/推导/变量间接/内联 if/元组表/枚举值 dict）。现覆盖三类**入站映射结构**：
#   ① dict 的 key；② 比较（==/!=/in）与三元条件的常量；③ set/list/tuple 字面量元素。
# 不覆盖且**属合法出口面**（故不列棘轮）：`schema.EXCHANGE_TS_MAP["SSE"]="SH"`（vt→ts 出站）、
# `md_gateway` 的 {1:"SSE"}（vnpy 枚举**值**）、tushare `trade_cal(exchange="SSE")`（驱动参数）、
# `scheduler` SQL CASE 的 "WHEN 'SSE'"（历史行 vt→ts，字符串常量非结构位）。
_BASELINE_COUNT = 0
_ALLOWED = frozenset()       # 只降不升：新增入站映射只能改 symbology


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _find_sse_mappings(tree: ast.AST) -> set[int]:
    """三类入站映射结构中出现常量 "SSE" 的行号集合（docstring 除外）。"""
    doc = _docstring_ids(tree)

    def is_sse(n) -> bool:
        return (isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value.strip().upper() == "SSE" and id(n) not in doc)

    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Dict, ast.DictComp)):
            for k in (node.keys if isinstance(node, ast.Dict) else [node.key]):
                if is_sse(k):
                    lines.add(node.lineno)
        elif isinstance(node, (ast.Compare, ast.IfExp)):
            for sub in ast.walk(node):
                if is_sse(sub):
                    lines.add(node.lineno)
        elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            for el in node.elts:
                if is_sse(el):
                    lines.add(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("dict", "OrderedDict", "defaultdict"):
            for kw in node.keywords:
                if kw.arg and kw.arg.strip().upper() == "SSE":
                    lines.add(node.lineno)
    return lines


def _scan(tree: ast.AST, rel: str) -> set[tuple[str, int]]:
    return {(rel, ln) for ln in _find_sse_mappings(tree)}


def test_sse_mapping_guard_positive_control():
    """正向控制：检测器必须能抓到 symbology 自己那张表（否则棘轮是空转）。"""
    sym = (_SRC / "quant_common/symbology.py").read_text(encoding="utf-8")
    assert _find_sse_mappings(ast.parse(sym)), "检测器未识别 symbology._VENDOR_ALIASES——守门空转"


def test_no_sse_inbound_mapping_outside_symbology():
    actual: set[tuple[str, int]] = set()
    for p in _SRC.rglob("*.py"):
        rel = str(p.relative_to(_SRC))
        if rel == "quant_common/symbology.py":
            continue      # 唯一合法映射表
        try:
            actual |= _scan(ast.parse(p.read_text(encoding="utf-8")), rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
    actual -= _ALLOWED
    assert len(actual) <= _BASELINE_COUNT, f"新增入站 SSE 映射（应改 symbology.vt_suffix）: {sorted(actual)}"
    assert len(actual) == _BASELINE_COUNT, f"棘轮条数不符（收口后同步下调基线）: {sorted(actual)}"


# ——— 变体覆盖自证（防检测器退化）———
@pytest.mark.parametrize("src", [
    'M = {"SSE": "SHSE"}',
    'M = dict(SSE="SHSE")',
    'M = {k: v for k, v in [("SSE", "SHSE")]}',
    'def f(e): return "SHSE" if e == "SSE" else e',
    'M = {"SSE": Exchange.SSE}',
    'PAIRS = (("SSE", "SHSE"),)',
    'OK = "SSE" in {"SHSE", "SSE"}',
])
def test_detector_catches_variants(src):
    assert _find_sse_mappings(ast.parse(src)), f"检测器漏了变体: {src}"


def test_detector_ignores_docstring_and_driver_kwarg():
    src = '"""doc: SSE → SHSE"""\nx = pro.trade_cal(exchange="SSE")\nSQL = "WHERE exchange=\'SSE\'"'
    assert not _find_sse_mappings(ast.parse(src))
