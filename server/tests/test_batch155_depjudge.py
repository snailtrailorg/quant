# -*- coding: utf-8 -*-
"""批 155 · P0-1 判定引擎钉（规则序 1-7b+drift 全分支+边界+has_update+evr+约束解析）。"""
import pytest

from src.quant_common.depjudge import (
    DEP_AUDIT_FRESH_H, judge, has_update_of, norm, parse_constraints,
    parse_names, in_constraint_max, evr_key)


class TestRuleOrder:
    def test_r1_local_first(self):
        assert judge("4.5.0+nogui", None, "4.6.0", None, True, "local")[0] == "unreachable"

    def test_r1_local_beats_drift(self):
        # local 恒先：即使约束漂移也 unreachable（fork 不比较）
        assert judge("9.9", "<2.0", "4.6", None, True, "local")[0] == "unreachable"

    def test_r2_none_coord(self):
        assert judge("1.0", None, "2.0", None, True, "none")[0] == "unknown"

    def test_r3_drift_before_latest_missing(self):
        # drift 判定不依赖上游（裁定审对调）
        assert judge("3.1.0", "<3.0", None, None, True, "pypi")[0] == "drift"

    def test_r4_latest_missing(self):
        assert judge("1.0", None, None, None, True, "pypi")[0] == "unknown"

    def test_r5_requires_python(self):
        assert judge("1.0", None, "2.0", None, False, "pypi")[0] == "unreachable"

    def test_r6_current_equal(self):
        assert judge("1.0", None, "1.0", None, True, "pypi")[0] == "current"

    def test_r6_current_latest_older(self):
        # 上游比当前旧（仓库坐标低）-> current 不主张
        assert judge("18.4", None, "13.23", None, True, "repo")[0] == "current"

    def test_r7a_actionable_in_constraint(self):
        assert judge("2.2.0", "<3.0", "2.3.1", None, True, "pypi")[0] == "actionable"

    def test_r7a_actionable_no_constraint(self):
        assert judge("1.0", None, "1.1", None, True, "pypi")[0] == "actionable"

    def test_r7b_in_constraint(self):
        assert judge("2.2.0", "<3.0", "3.1.0", "2.3.1", True, "pypi")[0] == "in_constraint"

    def test_r7b_pinned_when_no_candidate(self):
        assert judge("2.3.1", "<3.0", "3.1.0", None, True, "pypi")[0] == "pinned"

    def test_r7b_pinned_when_icm_not_greater(self):
        # in_constraint_max 存在但不 >cur -> pinned
        assert judge("2.3.1", "<3.0", "3.1.0", "2.3.1", True, "pypi")[0] == "pinned"


class TestBoundaries:
    def test_pep440_local_segment_trap(self):
        # 4.5.0+nogui != 4.5.0（local 段参与比较）——H1 场景已被规则 1 拦
        assert judge("4.5.0+nogui", None, "4.5.0", None, True, "pypi")[0] == "current"

    def test_cur_invalid_version(self):
        assert judge("garbage!!", None, "1.0", None, True, "pypi")[0] == "unknown"

    def test_latest_invalid_version(self):
        assert judge("1.0", None, "garbage!!", None, True, "pypi")[0] == "unknown"

    def test_internal_error_safe(self):
        # requires_python_ok 传非法对象不应外溢
        assert judge("1.0", None, "2.0", None, None, "pypi")[0] in ("current", "actionable")

    def test_fresh_constant_single_source(self):
        assert DEP_AUDIT_FRESH_H == 30


class TestV25Pins:
    """v2.5 专家三钉：constraint=None 语义 / 全 yanked / version-不变幂等。"""

    def test_constraint_none_no_drift(self):
        # 专家#3：None=无约束（fail-open），绝不触发 drift；走 7a
        assert judge("1.0", None, "2.0", None, True, "pypi")[0] == "actionable"
        assert judge("1.0", "", "2.0", None, True, "pypi")[0] == "actionable"

    def test_all_yanked_no_versions(self):
        # 专家#4：采集侧全 yanked → versions 空 → latest=None → unknown
        assert judge("1.0", None, None, None, True, "pypi")[0] == "unknown"

    def test_version_unchanged_last_changed_stable(self):
        # 专家四类：同日复跑 version 不变 → else 分支 SQL 不推 last_changed
        import inspect
        from src.health_monitor import security_collector as sc
        src = inspect.getsource(sc._upsert_components)
        # 定位第三个 execute（else 分支）：其 SQL 以 UPDATE...SET last_seen 开头且无 last_changed 赋值
        sqls = [ln for ln in src.split(chr(10)) if "UPDATE security_component" in ln or "last_changed=%s" in ln]
        # UPDATE 两处（变更分支含 last_changed=%s；else 分支不含）
        assert any("last_changed=%s" in x for x in sqls)
        # else 分支 SQL 以 "SET last_seen=%s, origin=%s" 开头且不含 last_changed
        else_sql = [x for x in sqls if "SET last_seen=%s" in x]
        assert else_sql
        assert all("last_changed" not in x for x in else_sql)


class TestHasUpdate:
    @pytest.mark.parametrize("cls,want", [
        ("actionable", "yes"), ("in_constraint", "yes"),
        ("pinned", "no"), ("current", "no"), ("drift", "no"),
        ("unreachable", "no"), ("unknown", "no"), (None, "unknown"), ("", "unknown"),
    ])
    def test_map(self, cls, want):
        assert has_update_of(cls) == want


class TestConstraints:
    def test_parse_with_bounds(self):
        c = parse_constraints("pandas>=2.0,<3.0\nnumpy>=2.4,<2.5\nfastapi")
        assert set(c) == {"pandas", "numpy"}
        assert "<3.0" in c["pandas"] and ">=2.0" in c["pandas"]   # 序列化顺序无关（SpecifierSet 迭代序）

    def test_parse_names_dev(self):
        assert "ruff" in parse_names("ruff\npytest-mock # c\n!!bad")
        assert parse_names("!!bad") == []

    def test_norm(self):
        assert norm("Typing_extensions") == "typing-extensions"
        assert norm("PyYAML") == "pyyaml"

    def test_icm(self):
        assert in_constraint_max(["2.2.0", "2.3.1", "3.1.0"], "<3.0", "2.2.0") == "2.3.1"
        assert in_constraint_max(["3.1.0"], "<3.0", "2.3.1") is None
        assert in_constraint_max(["1.0"], None, "0.9") is None  # 无约束不用 icm


class TestEvr:
    def test_numeric_beats_alpha(self):
        assert evr_key(None, "1.1.11", None) > evr_key(None, "1.1.1k", None)  # rpm 语义

    def test_epoch_dominates(self):
        assert evr_key("1", "0.9", None) > evr_key("0", "9.9", None)

    def test_release_breaks_tie(self):
        assert evr_key(None, "1.0", "2.el8") > evr_key(None, "1.0", "1.el8")

    def test_tilde_pre(self):
        from src.quant_common.depjudge import evr_compare
        assert evr_compare((None, "1.0~rc1", None), (None, "1.0", None)) < 0

    def test_release_alpha_suffix(self):
        assert evr_key(None, "1.1.1", "13.el8") > evr_key(None, "1.1.1", "13.ael8")
