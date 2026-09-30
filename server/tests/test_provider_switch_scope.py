"""批 83b：provider 切换范围裁定钉（P1-4）——「只有 bar 族可切源」。

**裁定原文**（任务书 83b 盲审 P1-4）：`delete_by_sync_item` 只支持 5 个 bar 族 sync_id
（`_PER_SYMBOL_META`，不含 index_daily），`switch_provider_api` 对 tier1/静态/日历/index_daily
会 rollback+500。**裁定**：83b 只对 bar 族提供切换，非 bar 项 provider 恒 tushare 且前端下拉
禁用（现状 `DataManage.vue` 的 `:disabled="providerOptions(row).length <= 1"` 已实现禁用态），
不扩 `delete_by_sync_item`。

**三面一致**（本文件把裁定钉成可执行断言）：
1. **能力面**——非 bar 同步项只被 tushare 的能力集声明（别的源不该声称能提供）。
2. **切换面**——`delete_by_sync_item` 的表清单（`_PER_SYMBOL_META`）只含 bar 族键；
   非 bar 键不得进入（否则切换会 rollback+500 或删错表）。
3. **前端面**——下拉可用性由「能力矩阵里该 sync_id 的供源数」派生，故 1 成立即禁用态成立。

这三面是同一事实的三种投影；任何一面单独漂移都会造成"界面允许切但后端 500"或反之。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


class TestSwitchableScope:
    """可切源的 sync_id 集合 = bar 族；非 bar 一律 tushare 独占。"""

    def test_per_symbol_meta_covers_only_bar_family(self):
        """`_PER_SYMBOL_META`（delete_by_sync_item 支持的表清单）必须 ⊆ bar 族 6 键。"""
        from src.data_sync.engine import _PER_SYMBOL_META, _VIA_KIND_IDS
        assert set(_PER_SYMBOL_META) <= set(_VIA_KIND_IDS), (
            f"delete_by_sync_item 支持表里混入非 bar 族键：{sorted(set(_PER_SYMBOL_META) - set(_VIA_KIND_IDS))}")

    def test_index_daily_is_bar_family_but_not_switchable(self):
        """index_daily 属 bar 族（`_VIA_KIND_IDS`）但**不在** `_PER_SYMBOL_META`——
        它是区间式全量、不是 per-symbol，故 delete_by_sync_item 不支持它（P1-4 明示）。
        本钉把这个"看起来该支持其实不支持"的例外固定下来，防有人按名字归类后放开切换。"""
        from src.data_sync.engine import _PER_SYMBOL_META, _VIA_KIND_IDS
        assert "index_daily" in _VIA_KIND_IDS
        assert "index_daily" not in _PER_SYMBOL_META

    def test_non_bar_items_are_tushare_exclusive(self):
        """非 bar 同步项（`_HANDLERS`）只被 tushare 的能力集声明。

        若哪天第二数据源声称能提供 `trade_cal`/`stk_limit_sync` 等，则前端下拉会放开，
        而后端 `delete_by_sync_item` 仍不支持 → 用户点下去 500。故此断言是"要么补齐
        后端支持、要么别声称能力"的强制二选一。
        """
        from src.data_platform.adapters.base import _ADAPTERS
        from src.data_sync.engine import _HANDLERS
        offenders = {}
        for provider, cls in _ADAPTERS.items():
            claimed = set(cls.capabilities) & set(_HANDLERS)
            if claimed and provider != "tushare":
                offenders[provider] = sorted(claimed)
        assert not offenders, (
            f"这些非 tushare 源声称可提供**非 bar** 同步项：{offenders}——"
            f"非 bar 项 provider 恒 tushare（P1-4）；要么撤掉能力声明，要么先扩 delete_by_sync_item")

    def test_bar_items_are_switchable_by_at_least_one_provider(self):
        """反向：bar 族键必须至少被一个源声明（否则它永远只能跑 tushare 之外的空菜单）。"""
        from src.data_platform.adapters.base import _ADAPTERS
        from src.data_sync.engine import _VIA_KIND_IDS
        declared = set().union(*[set(c.capabilities) for c in _ADAPTERS.values()]) if _ADAPTERS else set()
        missing = set(_VIA_KIND_IDS) - declared
        assert not missing, f"bar 族键无人声明能力（菜单会空）：{sorted(missing)}"

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_capability_matrix_shape_for_frontend(self):
        """前端下拉数据源（/api/datasource/capabilities）的形状：provider → sync_id 列表。

        前端 `providerOptions(row)` = 该列表含 row.id 的 provider 集；长度 ≤1 即禁用。
        故空能力源的 provider 会**出现**在矩阵里但列表为空 → 不参与任何行 → 不放开任何下拉。
        """
        from src.data_platform.adapters.base import _ADAPTERS
        matrix = {p: sorted(c.capabilities) for p, c in _ADAPTERS.items()}
        assert "tushare" in matrix and matrix["tushare"], "tushare 能力矩阵不得为空"
        # 空能力 stub 源：出现在矩阵里但列表为空（前端 filter 后自然不入选）
        for stub in ("joinquant", "ricequant"):
            if stub in matrix:
                assert matrix[stub] == [], f"{stub} 应为空能力 stub，实际 {matrix[stub]}"
