"""批 164a：标的筛选字段注册表断言（双盲 B-P0-3 验收——可回归）。

断言：品类词表 ⊆ ASSET_CATEGORIES 单源；字段结构合法（类型枚举+定义非空）。
"""
from src.data_platform.screen import FIELDS, fields_for
from src.quant_common.markets import ASSET_CATEGORIES


def test_categories_subset_of_single_source():
    """双盲 A-P0-1：品类词表禁自造——必须 ⊆ ASSET_CATEGORIES。"""
    assert set(FIELDS) <= set(ASSET_CATEGORIES), f"自造品类: {set(FIELDS) - set(ASSET_CATEGORIES)}"


def test_field_structure_valid():
    """六元组（补审 P1-6）：label/source/type/nullable/group 必填+定义面随 source 匹配。"""
    for cat, fields in FIELDS.items():
        # listed_fund 期 1 诚实降级（B-P0-2 待核非空率）——其余品类禁空集
        if cat != "listed_fund":
            assert fields, f"{cat} 空集"
        for key, f in fields.items():
            assert f.get("label"), f"{cat}.{key} 缺 label"
            assert f.get("source") in ("column", "cache", "expr"), f"{cat}.{key} source 非法"
            assert f.get("type") in ("num", "bool"), f"{cat}.{key} type 非法"
            assert isinstance(f.get("nullable"), bool), f"{cat}.{key} 缺 nullable"
            assert f.get("group") in ("valuation", "size", "quality", "tech", "other"), f"{cat}.{key} group 非法"
            src = f.get("source")
            if src == "column":
                assert f.get("col"), f"{cat}.{key} source=column 缺 col"
            elif src == "cache":
                assert f.get("factor"), f"{cat}.{key} source=cache 缺 factor"
            else:
                assert f.get("expr"), f"{cat}.{key} source=expr 缺 expr"


def test_unknown_category_rejected():
    try:
        fields_for("astock")
        raise AssertionError("astock 自造词应被拒")
    except ValueError:
        pass
