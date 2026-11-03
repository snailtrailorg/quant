"""批 164c：横截面 DSL 校验通道+求值器单测（任务 v2 验收）。"""
import pytest
from src.strategy_framework.cross_section import (
    validate_cross_section_expr, _eval_scalar, _XSEC_NUM_FUNCS, _XSEC_VECTOR_FUNCS)
import ast


def _eval(expr, env):
    return _eval_scalar(ast.parse(expr, mode="eval").body, env)


def test_valid_expressions():
    for e in ["pe_ttm < 30", "pe_ttm < 30 and circ_mv > 100000",
              "not (pe_ttm > 100)", "abs(dv_ratio) > 1"]:
        validate_cross_section_expr(e, "stock")


def test_unknown_field():
    with pytest.raises(ValueError, match="XSEC_UNKNOWN_FIELD"):
        validate_cross_section_expr("nope > 1", "stock")


def test_forbidden_func():
    with pytest.raises(ValueError, match="XSEC_FORBIDDEN_FUNC"):
        validate_cross_section_expr("sum(pe_ttm) > 1", "stock")


def test_nested_xsec_rejected():
    with pytest.raises(ValueError, match="XSEC_NESTED"):
        validate_cross_section_expr("rank(rank(pe_ttm)) > 0.5", "stock")


def test_syntax_attack_rejected():
    for bad in ["__import__('os')", "open('/etc/passwd')", "pe_ttm.__class__",
                "[x for x in range(3)]"]:
        with pytest.raises(ValueError, match="XSEC_"):
            validate_cross_section_expr(bad, "stock")


def test_too_many_calls():
    expr = " + ".join(["abs(pe_ttm)"] * 11) + " > 0"
    with pytest.raises(ValueError, match="XSEC_TOO_MANY"):
        validate_cross_section_expr(expr, "stock")


def test_scalar_semantics():
    env = {"pe_ttm": 20.0, "circ_mv": 500000.0}
    assert _eval("pe_ttm < 30", env) is True
    assert _eval("pe_ttm > 30", env) is False
    assert _eval("pe_ttm < 30 and circ_mv > 100000", env) is True
    assert _eval("pe_ttm < 10 or circ_mv > 100000", env) is True
    assert _eval("not (pe_ttm > 30)", env) is True
    assert _eval("abs(pe_ttm - 40)", env) == 20.0


def test_null_propagates_as_exception():
    env = {"pe_ttm": None}
    with pytest.raises(TypeError):
        _eval("pe_ttm < 30", env)


def test_word_lists_single_source():
    assert _XSEC_VECTOR_FUNCS == {"rank", "zscore", "percentile"}
    assert _XSEC_NUM_FUNCS == {"abs", "max", "min", "round"}
