"""评审 P1 修复回归钉（2026-11-03）：静默丢行面/放大面/并列秩/默认序。"""
import ast
import pytest
from src.strategy_framework.cross_section import (
    validate_cross_section_expr, _eval_scalar)


def _ev(expr, env):
    return _eval_scalar(ast.parse(expr, mode="eval").body, env)


def test_true_false_evaluable():
    """评审 P1-2：校验放行 true/false，求值必须认（否则静默丢行）。"""
    env = {"st_filter": True}
    assert _ev("st_filter == true", env) is True
    assert _ev("false", env) is False


def test_validator_eval_alignment():
    """凡校验通过的表达式必须可求值（同集白名单）——抽样十式。"""
    cases = [
        ("pe_ttm ＜ 30", None),
        ("pe_ttm + 1 ＞ 0", None),
        ("not (pe_ttm ＞ 100)", None),
        ("pe_ttm ＞ 5 and circ_mv ＞ 1000", None),
        ("pe_ttm ＞ 5 or circ_mv ＞ 1000", None),
        ("abs(pe_ttm) ＞ 1", None),
        ("pe_ttm % 2 ＜ 1", None),
        ("-pe_ttm ＜ 0", None),
        ("st_filter == true", None),
        ("pe_ttm * 2 ＞ 10", None),
    ]
    env = {"pe_ttm": 7.0, "circ_mv": 2000.0, "st_filter": True}
    for expr, _ in cases:
        e = expr.replace("＜", "<").replace("＞", ">")
        validate_cross_section_expr(e, "stock")
        _ev(e, env)   # 不抛=对齐成立


def test_amplification_rejected():
    """评审 P1-1：幂/字符串/超大常量必须校验期拒绝。"""
    for bad in ["2 ** 10 ** 10 > 0",
                "pe_ttm * 1e12 > 0",
                "'x' * 100000 > pe_ttm"]:
        with pytest.raises(ValueError):
            validate_cross_section_expr(bad, "stock")


def test_unsupported_nodes_rejected_cleanly():
    """评审 P1-2：这些以前校验过、求值抛错（静默丢行）——现应校验期报错。"""
    for bad in ["+pe_ttm > 0", "pe_ttm // 2 > 0", "1 if pe_ttm else 2",
                "pe_ttm in (1, 2)", "pe_ttm is 1"]:
        with pytest.raises(ValueError):
            validate_cross_section_expr(bad, "stock")


def test_vector_arity_required():
    with pytest.raises(ValueError, match="XSEC_ARITY"):
        validate_cross_section_expr("rank() > 0.5", "stock")
