"""批 83b：provider 切换范围裁定钉（P1-4）——「可切源」集合与其**后端前提**。

**裁定原文**（任务书 83b 盲审 P1-4）：`delete_by_sync_item` 只支持 5 个 bar 族 sync_id
（`_PER_SYMBOL_META`，不含 index_daily），`switch_provider_api` 对 tier1/静态/日历/index_daily
会 rollback+500。**裁定**：83b 只对 bar 族提供切换，非 bar 项 provider 恒 tushare 且前端下拉
禁用（现状 `DataManage.vue` 的 `:disabled="providerOptions(row).length <= 1"` 已实现禁用态）。

**批 107 演进**：供给面 7 项（`_LITERAL_SUPPLY`）经 adapter 收编后**可切源**，
前提＝`delete_by_sync_item` 新增「清整表 + 重置游标」分支支持它们（**先扩 delete，再开能力**）。

**三面一致**（本文件把裁定钉成可执行断言）：
1. **能力面**——可切的非 bar 同步项（被 **≥2 个源**声明）必须 ⊆ `delete_by_sync_item` 支持集
   （批 107 起＝`_LITERAL_SUPPLY`）。
2. **切换面**——`delete_by_sync_item` 的 per-symbol 清单（`_PER_SYMBOL_META`）只含 bar 族键；
   供给面项走独立分支（清整表）。
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

    def test_switchable_nonbar_must_have_delete_support(self):
        """非 bar 同步项**可切源的前提**＝`delete_by_sync_item` 支持它。

        判据演进（批 101）：原判据「非 bar 项恒 tushare」太粗——它把 binance 独占的
        `binance_perp_daily` 误伤；改为同源判据「供源数 ≥2 即可切，故必须后端支持」。
        判据**再演进（批 107）**：把供给面 7 项（`_LITERAL_SUPPLY`：astock_basic/astock_list/
        static_symbols/cb_basic/convertible_terms/etf_list/trade_cal）纳入 `delete_by_sync_item`
        （新增「清整表 + 重置游标」分支）⇒ 它们**可以**可切源。

        故断言＝**可切的非 bar 项必须 ⊆ delete 支持集**（`_LITERAL_SUPPLY`）；
        否则＝「前端下拉放开 → `switch_provider_api` → rollback+500」。

        若将来要让更多项可切：**先扩 `delete_by_sync_item` 的支持集，再开能力**。
        """
        from src.data_platform.adapters.base import _ADAPTERS
        from src.data_sync import engine
        claim: dict[str, list[str]] = {}
        for provider, cls in _ADAPTERS.items():
            for sid in set(cls.capabilities) & set(engine._HANDLERS):
                claim.setdefault(sid, []).append(provider)
        switchable = {sid: sorted(ps) for sid, ps in claim.items() if len(ps) > 1}
        supported = set(engine._LITERAL_SUPPLY)
        bad = {sid: ps for sid, ps in switchable.items() if sid not in supported}
        assert not bad, (
            f"这些非 bar 项被多个源声明但 `delete_by_sync_item` 不支持"
            f"（前端下拉会放开而后端 500）：{bad}；要么撤掉能力声明，要么先扩 delete_by_sync_item")

    def test_literal_items_have_delete_support(self):
        """批 107：供给面 7 项在 `delete_by_sync_item` 有「清整表」专用分支（非 error）。"""

        class _Cur:
            rowcount = 0

        class _Conn:
            def __init__(self):
                self.sqls: list[str] = []

            def execute(self, sql, params=None):
                self.sqls.append(sql)
                return _Cur()

        from src.data_sync.engine import _LITERAL_SUPPLY, delete_by_sync_item
        for sid, (_k, _s, tbl) in _LITERAL_SUPPLY.items():
            c = _Conn()
            r = delete_by_sync_item(sid, conn=c)
            assert r["status"] == "success", (sid, r)
            assert any(f'DELETE FROM "{tbl}"' in s for s in c.sqls), (sid, c.sqls)
            assert any("UPDATE sync_config" in s for s in c.sqls), (sid, c.sqls)

    def test_unknown_item_still_rejected(self):
        """未支持项仍响亮拒绝（不静默假删）。"""
        from unittest.mock import MagicMock
        from src.data_sync.engine import delete_by_sync_item
        r = delete_by_sync_item("no_such_sync_id", conn=MagicMock())
        assert r["status"] == "error" and "不支持删除" in r["error"]

    def test_binance_perp_daily_is_binance_exclusive(self):
        """批 101 落点：`binance_perp_daily` 是非 bar 项且 **binance 独占**（tushare 不得声称）。"""
        from src.data_platform.adapters.base import _ADAPTERS
        claim = [p for p, c in _ADAPTERS.items() if "binance_perp_daily" in set(c.capabilities)]
        assert claim == ["binance"], claim
        assert "binance_perp_daily" not in set(_ADAPTERS["tushare"].capabilities)

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
        批 103b：joinquant 由空能力 stub 毕业（声明 astock_daily_jq），故移出 stub 组，
        锚定其能力为**聚宽独占**的 astock_daily_jq（不得与 tushare 撞键，否则下拉放开→后端 500）。
        批 107：joinquant 再增 `astock_list`（首个「非 bar 多源」样板）——**此处撞键是设计意图**
        （`astock_list` 由此可切源），前提已由 `delete_by_sync_item` 的清整表分支满足
        （见 `test_literal_items_have_delete_support`）。
        """
        from src.data_platform.adapters.base import _ADAPTERS
        matrix = {p: sorted(c.capabilities) for p, c in _ADAPTERS.items()}
        assert "tushare" in matrix and matrix["tushare"], "tushare 能力矩阵不得为空"
        assert matrix["joinquant"] == ["astock_daily_jq", "astock_list"], matrix.get("joinquant")
        # 空能力 stub 源：出现在矩阵里但列表为空（前端 filter 后自然不入选）
        for stub in ("ricequant",):
            if stub in matrix:
                assert matrix[stub] == [], f"{stub} 应为空能力 stub，实际 {matrix[stub]}"
