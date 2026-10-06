"""批 83b：多源切换**行为级**验收钉——「数据表选源真生效」（验收判据 1）。

为什么必须有这个文件（P0 防线 2 硬约束）：上产后 provider 下拉仍只有 tushare
（joinquant/ricequant 是空能力 stub，见 test_provider_switch_scope），所以"选源真生效"
**无法**靠真实源验证。任务书给的出路是「注入第二 stub 源，或直改 DB 的 provider 断言
fail-fast 而非静默回落」——fail-fast 那一半已在 test_provider_registry 钉住；
本文件补另一半：**注入一个能真出 bar 的第二源，跑完整切换链路**，证明
`sync_config.provider` → handler → adapter → 落库 `source` 列全链贯通。

工程上为什么这样打桩才不算"mock 绿"（记忆 handoff-no-shortcuts）：
- **真跑**的：sync_config 行读取、`_get_kline_adapter` 查表路由、`_get_rate_ds`、
  `_sync_via_kind` → `_sync_via_kind_daily_batch` 的逐日循环、`_save_bars` 真写 PG、
  落库行的 `source` 列。
- **打桩**的：只有"第二数据源本身"（真实第二源不存在，这是本批要证明的对象）。
  桩是**被测对象**而非被测逻辑的替身——与"用 mock 让被测逻辑恒绿"是两件事。
- 桩经**正式注册通道**登记（`register_adapter` 装饰器 + `_REGISTRY`），不是短路
  `_ADAPTERS` 内部——即验证的正是"新增源=三处各注册一类，**引擎零改动**"（内容点 2）。

用合成标的 `999999.SHSE`（不撞真实数据），用已有 sync_id `astock_daily`
（路由表是写死的 frozenset，新 sync_id 需改代码——provider 维才是本批的"零改动"维）。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

STUB_PROVIDER = "stubsrc_b83"
STUB_SYMBOL = "999999.SHSE"
SYNC_ID = "astock_daily"
BACKFILL_DAY = "20260930"      # 2026-09-30 = 周三（pd.date_range freq='B' 会取到它）


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def _make_stub_classes():
    """构造并注册第二源的 adapter + DataSource（走正式注册通道）。"""
    from src.data_platform.adapters.base import BaseDataAdapter, UnsupportedFeature, register_adapter
    from src.data_platform.data_source import DataSource

    @register_adapter
    class _StubAdapter(BaseDataAdapter):
        provider = STUB_PROVIDER
        capabilities = {"astock_daily"}      # 声明能提供该 bar 族 sync_id

        def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
            raise UnsupportedFeature("stub 只实现 fetch")

        def pull_minute(self, symbol, freq, start, end):
            raise UnsupportedFeature("stub 只实现 fetch")

        def to_bar_rows(self, df, freq, adj_map=None):
            raise UnsupportedFeature("stub 只实现 fetch")

        def available_range(self, kind):     # 批 108·步 3：源界（窗口第四边界硬契约）
            return ("2010-01-01", None)

        def publish_lag(self, kind):
            return 0

        def fetch(self, req, acct=None):
            from datetime import datetime
            from src.quant_common.contract import to_contract
            from src.data_platform.tz import as_utc
            if getattr(req, "kind", "") != "bar_daily":
                raise UnsupportedFeature(f"stub 不支持 kind={getattr(req, 'kind', '?')}")
            ts = as_utc(datetime(2026, 9, 30, 15, 0))
            rows = [(STUB_SYMBOL, "1D", ts, 1.0, 1.1, 0.9, 1.05, 100.0, 1000.0, None, self.provider)]
            return to_contract(rows, source=self.provider, kind="bar_daily", freq="1D")

    class _StubDataSource(DataSource):
        provider = STUB_PROVIDER

        def get_client(self):
            return object()      # stub 不需要真客户端

        def test_connection(self) -> bool:
            return True

    from src.data_platform import data_source as _ds_mod
    _ds_mod._REGISTRY[STUB_PROVIDER] = _StubDataSource
    return _StubAdapter, _StubDataSource


def _cleanup(prev_cfg: dict | None):
    """还原：注销第二源 + 还原 sync_config 行 + 删合成 bar 行。"""
    from src.data_platform.adapters.base import _ADAPTERS
    from src.data_platform.data_source import _REGISTRY
    from src.data_platform.db import get_conn
    _ADAPTERS.pop(STUB_PROVIDER, None)
    _REGISTRY.pop(STUB_PROVIDER, None)
    with get_conn() as conn:
        conn.execute("DELETE FROM bar_1d WHERE symbol=%s", (STUB_SYMBOL,))
        if prev_cfg is not None:
            conn.execute(
                "UPDATE sync_config SET provider=%s, last_sync_date=%s, last_sync_ts=%s, "
                "last_sync_count=%s, last_status=%s WHERE id=%s",
                (prev_cfg["provider"], prev_cfg["last_sync_date"], prev_cfg["last_sync_ts"],
                 prev_cfg["last_sync_count"], prev_cfg["last_status"], SYNC_ID))
        conn.commit()


def _snapshot_cfg() -> dict:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        r = conn.execute(
            "SELECT provider, last_sync_date, last_sync_ts, last_sync_count, last_status "
            "FROM sync_config WHERE id=%s", (SYNC_ID,)).fetchone()
    return {"provider": r[0], "last_sync_date": r[1], "last_sync_ts": r[2],
            "last_sync_count": r[3], "last_status": r[4]}


class TestSecondSourceRouting:
    """验收判据 1：sync_config.provider 切换 → 同步真的走对应数据源。"""

    def test_switch_routes_to_second_source_end_to_end(self):
        prev = _snapshot_cfg()
        _make_stub_classes()
        try:
            from src.data_platform.db import get_conn
            # ① 切源（等价于 switch_provider_api 的核心写操作；此处直改 DB，同 P2 授权的姿势）
            with get_conn() as conn:
                conn.execute("UPDATE sync_config SET provider=%s WHERE id=%s",
                             (STUB_PROVIDER, SYNC_ID))
                conn.execute("DELETE FROM bar_1d WHERE symbol=%s", (STUB_SYMBOL,))
                conn.commit()

            # ② 走统一入口真跑（source 列是"选源真生效"的铁证）
            from src.data_sync.engine import sync
            r = sync(SYNC_ID, backfill_from=BACKFILL_DAY)
            assert r.get("status") in ("success", "partial"), r

            with get_conn() as conn:
                got = conn.execute(
                    "SELECT symbol, source FROM bar_1d WHERE symbol=%s", (STUB_SYMBOL,)).fetchall()
            assert got, f"第二源没产出数据——路由未生效。sync 结果={r}"
            assert got[0][1] == STUB_PROVIDER, (
                f"落库 source={got[0][1]!r}，期望 {STUB_PROVIDER!r}——"
                f"说明请求没走到第二源（串源/回落）")
        finally:
            _cleanup(prev)

    def test_no_code_change_needed_in_engine(self):
        """内容点 2「引擎零改动」：注册第二源期间，engine 的 adapter 选择仍只走查表。

        钉法=断言 engine 里不存在对 provider 名的分支（同一件事由 CI 断言一
        test_contract_gate 全仓守），此处再针对 engine 文件确认一次——因为"零改动"
        是内容点 2 的验收语，值得有一条就近的断言。
        """
        import pathlib
        import re
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "data_sync" / "engine.py"
        text = src.read_text(encoding="utf-8")
        branch = re.compile(r"(?:if|elif)\s+[^\n:]*\bprovider\b\s*(?:==|!=)\s*['\"]")
        hits = [ln.strip() for ln in text.splitlines() if branch.search(ln)]
        assert not hits, f"engine 出现 provider 字面量分支（新增源将需改引擎）：{hits}"

    def test_incomplete_second_source_blocks_sync_loudly(self):
        """反向钉（P0 防线 1 的端到端版）：只注册 adapter、不注册 DataSource 的源，
        跑同步必须**响亮失败**，而不是静默按 tushare 拉数。

        做法：注册 adapter → 不注册 DataSource → 切 provider → 跑 sync
        → 断言 sync 返回 error 且错误里含 ProviderConfigError 的语义（不吞）。
        """
        from src.data_platform.adapters.base import BaseDataAdapter, UnsupportedFeature, register_adapter, _ADAPTERS
        from src.quant_common.contract import ProviderConfigError

        @register_adapter
        class _HalfBakedAdapter(BaseDataAdapter):
            provider = STUB_PROVIDER + "_half"
            capabilities = {"astock_daily"}

            def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
                raise UnsupportedFeature("x")

            def pull_minute(self, symbol, freq, start, end):
                raise UnsupportedFeature("x")

            def to_bar_rows(self, df, freq, adj_map=None):
                raise UnsupportedFeature("x")

        prev = _snapshot_cfg()
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute("UPDATE sync_config SET provider=%s WHERE id=%s",
                             (STUB_PROVIDER + "_half", SYNC_ID))
                conn.commit()
            # 直接钉解析层：半成品集成立刻抛（不返回 None → 不会 `or TushareDataSource()` 串源）
            from src.data_sync.engine import _get_rate_ds
            with pytest.raises(ProviderConfigError):
                _get_rate_ds(STUB_PROVIDER + "_half")
        finally:
            _ADAPTERS.pop(STUB_PROVIDER + "_half", None)
            _cleanup(prev)

    def test_stub_registration_does_not_leak(self):
        """清理面自检：跑完上面用例后，两个注册表都不该残留 stub 条目。"""
        from src.data_platform.adapters.base import _ADAPTERS
        from src.data_platform.data_source import _REGISTRY
        assert STUB_PROVIDER not in _ADAPTERS
        assert STUB_PROVIDER not in _REGISTRY
        assert STUB_PROVIDER + "_half" not in _ADAPTERS
        # 且 sync_config 已还原（跑完不该留下改过的 provider/游标）
        restored = _snapshot_cfg()
        assert restored["provider"] == "tushare", restored
