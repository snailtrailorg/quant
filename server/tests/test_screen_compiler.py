"""批 164b 件4：条件编译器单测（可回归——任务 v3 验收清单）。"""
import pytest
from src.data_platform.screen import compile_conditions, build_base_from


def test_compile_all_basic():
    d = {"v": 1, "match": "all", "rows": [
        {"field": "pe_ttm", "op": "<", "value": 30},
        {"field": "circ_mv", "op": ">=", "value": 500000}]
    }
    where, params, notes = compile_conditions("stock", d)
    assert "AND" in where and "pe_ttm IS NOT NULL" in where
    assert params == [30, 500000] and set(notes) == {"pe_ttm", "circ_mv"}


def test_compile_any():
    d = {"v": 1, "match": "any", "rows": [{"field": "pe", "op": "<", "value": 10}]}
    where, _, _ = compile_conditions("stock", d)
    assert where.startswith(" WHERE pe IS NOT NULL")


def test_bool_only_true():
    d = {"v": 1, "match": "all", "rows": [{"field": "st_filter", "op": "=", "value": True}]}
    where, params, _ = compile_conditions("stock", d)
    assert "st_filter = true" in where and not params
    # 极性陷阱负例
    with pytest.raises(ValueError):
        compile_conditions("stock", {"v": 1, "match": "all",
                                      "rows": [{"field": "st_filter", "op": "=", "value": False}]})


def test_percentile_direction_and_bounds():
    d = {"v": 1, "match": "all", "rows": [{"field": "circ_mv", "op": "percentile_top", "value": 80}]}
    where, params, _ = compile_conditions("stock", d)
    assert "percentile_cont(0.8)" in where and ">=" in where
    with pytest.raises(ValueError):
        compile_conditions("stock", {"v": 1, "match": "all",
                                      "rows": [{"field": "circ_mv", "op": "percentile_top", "value": 101}]})


def test_injection_rejected():
    for bad_field in ["d.pe; DROP TABLE users", "pe --"]:
        with pytest.raises(ValueError):
            compile_conditions("stock", {"v": 1, "match": "all",
                                          "rows": [{"field": bad_field, "op": "<", "value": 1}]})
    for bad_op in ["; DELETE", "LIKE"]:
        with pytest.raises(ValueError):
            compile_conditions("stock", {"v": 1, "match": "all",
                                          "rows": [{"field": "pe", "op": bad_op, "value": 1}]})


def test_unknown_category():
    with pytest.raises(ValueError):
        compile_conditions("astock", {"v": 1, "match": "all", "rows": []})
