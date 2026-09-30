"""批 75·H7 行为级验收钉（验收标准 1/3）：local_pg 缺数据 → fetch-on-miss 真拉第二 stub 源并落仓。

手法=批 83b test_multi_source_switch 同款（真 stub adapter + 真 DB + 真 DataBus，禁 mock 绿）：
- **真跑**的：db.get_bars 空→DataGap→routing.resolve（真 data_source 行的能力过滤/SM 覆盖/
  排序/审计落库）→ chain.fetch skip local_pg → stub adapter.fetch（consume 口径，req.mode 分派）
  → store.save 真写 bar_1d → 落库行 `source` 列=stub；**第二次 get_bars 走 local 命中**
  （仓即候选①闭环——数据真回到仓里）。
- **打桩**的：只有「第二数据源本身」（真实第二源不存在；桩=被测对象，非被测逻辑替身）。

停牌缺根（验收标准 3）：stub 返回空帧（区间内一根 bar 都没有）→ get_bars 返回空帧、
不落仓、不崩——缺根豁免=per-symbol 区间拉取的天然语义（2026-10-01 裁定，无独立豁免引擎）。
"""
import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

STUB_PROVIDER = "stubsrc_b75"
STUB_SYMBOL = "999998.SHSE"          # 合成标的（不撞真实数据）
DS_ROW_NAME = "stub75-behavior"


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def _make_stub(bars: bool):
    """构造并注册第二源 adapter + DataSource（走正式注册通道）。bars=False=停牌（空帧）。"""
    from src.data_platform.adapters.base import BaseDataAdapter, UnsupportedFeature, register_adapter
    from src.data_platform.data_source import DataSource

    @register_adapter
    class _StubAdapter(BaseDataAdapter):
        provider = STUB_PROVIDER
        capabilities = {"astock_daily"}       # 路由能力过滤走 KIND_CAP_CLASS 派生（bar_daily∈hist_quote）

        def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
            raise UnsupportedFeature("stub 只实现 fetch")

        def pull_minute(self, symbol, freq, start, end):
            raise UnsupportedFeature("stub 只实现 fetch")

        def to_bar_rows(self, df, freq, adj_map=None):
            raise UnsupportedFeature("stub 只实现 fetch")

        def fetch(self, req, acct=None):
            from src.quant_common.contract import to_contract
            if getattr(req, "kind", "") != "bar_daily":
                raise UnsupportedFeature(f"stub 不支持 kind={getattr(req, 'kind', '?')}")
            assert getattr(req, "mode", "consume") == "consume"   # 消费口径分派信号真到了 adapter
            if not bars:
                return to_contract([], source=self.provider, kind="bar_daily", freq="1D")
            from src.data_platform.tz import as_utc
            ts = as_utc(datetime(2026, 9, 30, 15, 0))
            rows = [(STUB_SYMBOL, "1D", ts, 1.0, 1.1, 0.9, 1.05, 100.0, 1000.0, None, self.provider)]
            return to_contract(rows, source=self.provider, kind="bar_daily", freq="1D")

    class _StubDataSource(DataSource):
        provider = STUB_PROVIDER

        def get_client(self):
            return object()

        def test_connection(self) -> bool:
            return True

    from src.data_platform import data_source as _ds_mod
    _ds_mod._REGISTRY[STUB_PROVIDER] = _StubDataSource
    return _StubAdapter, _StubDataSource


def _cleanup():
    """还原：删 data_source 行 + 删合成 bar 行 + 注销注册表条目。"""
    from src.data_platform.adapters.base import _ADAPTERS
    from src.data_platform.data_source import _REGISTRY
    from src.data_platform.db import get_conn
    _ADAPTERS.pop(STUB_PROVIDER, None)
    _REGISTRY.pop(STUB_PROVIDER, None)
    with get_conn() as conn:
        conn.execute("DELETE FROM bar_1d WHERE symbol=%s", (STUB_SYMBOL,))
        conn.execute("DELETE FROM data_source WHERE name=%s", (DS_ROW_NAME,))
        conn.commit()


def _req():
    from src.quant_common.contract import DataRequest
    # end 取 10 月（naive 按上海零点转 UTC=09-30 16:00Z，须盖过 09-30 收盘 bar 07:00Z）
    return DataRequest(kind="bar_daily", symbols=(STUB_SYMBOL,), temporality="historical",
                       freq="1D", range_=(datetime(2026, 9, 1), datetime(2026, 10, 10)))


class TestFetchOnMissBehavior:
    """验收 1：local_pg 缺数据的单标的，fetch-on-miss 真拉到 stub 数据并落库，source=stub。"""

    def test_miss_pulls_second_source_and_persists(self):
        _cleanup()                      # 幂等清场（先清 DB/注册表残留）
        _make_stub(bars=True)
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO data_source (name, provider, market, credentials_encrypted, "
                    "params, capabilities, position, enabled) VALUES (%s, %s, 'astock', NULL, "
                    "'{}'::jsonb, ARRAY['bar_daily'], -100, true)", (DS_ROW_NAME, STUB_PROVIDER))
                conn.commit()

            from src.data_platform.databus import DataBus
            # ① miss→真拉：本地无数据 → DataGap → 远端 stub 拉到 1 根
            frame, wm = DataBus().get_bars(_req())
            assert frame.rows, f"fetch-on-miss 没拉到数据：source={frame.source} rows={frame.rows}"
            assert frame.source == STUB_PROVIDER
            assert wm is not None

            # ② 落仓铁证：bar_1d 真有行且 source 列=stub
            with get_conn() as conn:
                got = conn.execute(
                    "SELECT symbol, source FROM bar_1d WHERE symbol=%s", (STUB_SYMBOL,)).fetchall()
            assert got and got[0][1] == STUB_PROVIDER, f"落库 source={got}"

            # ③ 仓即候选①：注销 stub 后再读 → local 命中同一份数据（不靠远端）
            from src.data_platform.adapters.base import _ADAPTERS
            from src.data_platform.data_source import _REGISTRY
            _ADAPTERS.pop(STUB_PROVIDER, None)
            _REGISTRY.pop(STUB_PROVIDER, None)
            with get_conn() as conn:
                conn.execute("DELETE FROM data_source WHERE name=%s", (DS_ROW_NAME,))
                conn.commit()
            frame2, _wm2 = DataBus().get_bars(_req())
            assert frame2.rows and frame2.source == STUB_PROVIDER
        finally:
            _cleanup()

    def test_suspended_symbol_empty_frame_no_crash(self):
        """验收 3：停牌缺根（区间内无 bar）→ 空帧返回、不落仓、不崩（缺根=天然语义）。"""
        _cleanup()
        _make_stub(bars=False)
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO data_source (name, provider, market, credentials_encrypted, "
                    "params, capabilities, position, enabled) VALUES (%s, %s, 'astock', NULL, "
                    "'{}'::jsonb, ARRAY['bar_daily'], -100, true)", (DS_ROW_NAME, STUB_PROVIDER))
                conn.commit()
            from src.data_platform.databus import DataBus
            frame, wm = DataBus().get_bars(_req())
            assert frame.rows == () and wm is None          # 空帧不崩
            with get_conn() as conn:                        # 不落仓（空帧无行可写）
                n = conn.execute("SELECT count(*) FROM bar_1d WHERE symbol=%s",
                                 (STUB_SYMBOL,)).fetchone()[0]
            assert n == 0
        finally:
            _cleanup()

    def test_no_registration_leak(self):
        """清理面自检：两个注册表与 DB 都无 stub 残留。"""
        from src.data_platform.adapters.base import _ADAPTERS
        from src.data_platform.data_source import _REGISTRY
        from src.data_platform.db import get_conn
        assert STUB_PROVIDER not in _ADAPTERS
        assert STUB_PROVIDER not in _REGISTRY
        with get_conn() as conn:
            n = conn.execute("SELECT count(*) FROM data_source WHERE name=%s",
                             (DS_ROW_NAME,)).fetchone()[0]
            m = conn.execute("SELECT count(*) FROM bar_1d WHERE symbol=%s",
                             (STUB_SYMBOL,)).fetchone()[0]
        assert n == 0 and m == 0
