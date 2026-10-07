"""批 109：per-symbol 旁路对账（beat 周日 03:33）——标的级差集 / 不确定门 / 限频重拉。

钉的是**行为**：

1. **期望集下界复用批 108 的 `_window_floors`**（§5.3-1「不许各算一套」）——下界＝
   `max(inception, retention, source_earliest)`；**不得**在本层另算一套。
2. **§5.4 两道不确定门，抑制一切缺口主张**：`inception` 未知（裁定 F：Binance `fapi` 被墙 ⇒
   `symbol_inception()` 返 `None`）⇒ `reason='inception_unknown'`；列表快照 age 超阈 ⇒
   `reason='stale_source'`（§九.1 依赖倒置的闭合口）。两者都**不主张缺口**。
3. **限频重拉**：`open` 行 `pull_count < 3` 且距 `last_seen ≥ 1 天` ⇒ 按缺口区间重拉；成功由
   视图式同步自然闭合（`closed`）；尝试计数 `pull_count` 递增。
4. **聚合告警只对“本轮新 open”**（防首跑全史告警风暴）。
5. **范围收窄的可见钉**：`pool_data`（财报/股东族）**不在** per-symbol 对账范围内——其「缺」不是
   「预期日无数据」（设计 §5.2 的「缺的定义」在判据层级先于 §7.1 的枚举）。若日后有人把它塞回来，
   本组必红，必须显式改判据（见任务文件 Q6）。

DB 依赖：`sync_gap` / `security_master` 查询走真库（无库自动跳过）；adapter 与读取口全 mock。
测试只写 `__t109s` 命名空间且用完即清。
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.data_sync import engine

NS = "__t109s"
SID = NS + "_binance_perp_daily"     # 测试用 sync_id（覆盖真实 scope 表）


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def _exec(sql: str, params: tuple = ()) -> None:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()


def _q(sql: str, params: tuple = ()) -> list:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def _rows(sid: str) -> list[tuple]:
    return [(r[0], str(r[1]), str(r[2]), r[3], r[4], r[5]) for r in _q(
        "SELECT symbol, gap_start, gap_end, state, reason, pull_count FROM sync_gap "
        "WHERE sync_id=%s ORDER BY symbol, gap_start", (sid,))]


def _clean() -> None:
    _exec("DELETE FROM sync_gap WHERE LEFT(sync_id, %s) = %s", (len(NS), NS))


@pytest.fixture(autouse=True)
def _clean_ns():
    try:
        _clean()
    except Exception:  # noqa: S110
        pass
    yield
    try:
        _clean()
    except Exception:  # noqa: S110
        pass


class _FakeCrypto:
    """crypto adapter 桩：三地板三源各由入参控制（对齐 `_window_floors` 的契约）。"""

    provider = "binance"
    venue = "BINANCE"

    def __init__(self, src_lo="2019-12-31", inception=None, lag=1):
        self._src_lo, self._inc, self._lag = src_lo, inception, lag
        self.fetched: list[dict] = []

    def available_range(self, kind):
        return (self._src_lo, None)

    def publish_lag(self, kind):
        return self._lag

    def symbol_inception(self, symbol):
        return self._inc.get(str(symbol)) if isinstance(self._inc, dict) else self._inc

    def fetch_supply(self, kind, sub_kind, *, symbol, start, end, freq):
        self.fetched.append({"symbol": symbol, "start": start, "end": end, "freq": freq})
        import pandas as pd
        return pd.DataFrame([{"ts": start, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])

    def to_bar_rows(self, df, freq):
        return [("X", 1, 2, 3, 4, 5, 6)]


def _run(adapter, *, universe, local, cfg_retention=None, today_end=date(2026, 10, 6),
         repull=None, alerts=None, tz="UTC", repull_error=None):
    """跑 `_reconcile_symbols`（scope 收窄成单条；DB 侧只碰 `__t109s` 命名空间）。

    `repull_error` 非空 ⇒ 该异常作为 `_repull_symbol` 的 side_effect（重拉失败路径）。**必须在
    本口注入**（不能在用例里再套一层 `patch.object`——内层 patch 后启动，会盖掉用例的 patch，
    使「重拉失败」静默变成「重拉成功」）。
    """
    cfg = {"id": SID, "enabled": True, "provider": "binance", "retention": cfg_retention}
    scope = {SID: ("bar_daily", "perp", "1D", "bar_1d", tz, "perp")}
    repull_calls = repull if repull is not None else []
    alert_calls = alerts if alerts is not None else []

    def _fake_repull(ad, kind, sub_kind, freq, src_sym, g0, g1):
        repull_calls.append((src_sym, g0, g1))
        return 1

    repull_se = repull_error if repull_error is not None else _fake_repull

    with patch.object(engine, "_RECONCILE_SYMBOL_SCOPE", scope), \
         patch.object(engine, "_get_config", return_value=cfg), \
         patch.object(engine, "_get_supply_adapter", return_value=adapter), \
         patch.object(engine, "_sm_universe", return_value=universe), \
         patch.object(engine, "_crypto_end", return_value=today_end), \
         patch.object(engine, "_local_dates", side_effect=lambda *a, **k: local), \
         patch.object(engine, "_trade_dates_in_range", return_value=None), \
         patch.object(engine, "_repull_symbol", side_effect=repull_se), \
         patch.object(engine, "_alert_sync_gaps",
                      side_effect=lambda label, spans: alert_calls.append((label, spans))):
        out = engine._reconcile_symbols()
    return out, repull_calls, alert_calls


# ---------------------------------------------------------------------------
# 1：范围收窄的可见钉（架构判据：§5.2「缺的定义」先于 §7.1 的族枚举）
# ---------------------------------------------------------------------------


class TestScope:
    def test_pool_data_excluded_from_symbol_reconcile(self):
        """财报/股东族（`pool_data`）**不进** per-symbol 对账：无「预期日」概念 ⇒ 日期级差集不适用。"""
        assert "pool_data" not in engine._RECONCILE_SYMBOL_SCOPE
        assert "pool_data_full_calibrate" not in engine._RECONCILE_SYMBOL_SCOPE

    def test_scope_covers_crypto_bar_family_and_minute(self):
        for sid in ("binance_perp_daily", "binance_perp_hourly", "binance_perp_1min",
                    "binance_perp_15min", "okx_perp_daily",
                    "astock_minute", "astock_minute_5min"):
            assert sid in engine._RECONCILE_SYMBOL_SCOPE

    def test_scope_tables_are_bar_tables(self):
        for sid, spec in engine._RECONCILE_SYMBOL_SCOPE.items():
            assert spec[3].startswith("bar_"), sid

    @needs_db
    def test_sm_universe_is_the_universe_source(self):
        """宇宙取自 `security_master`（裁定 Q3：前缀法漏 18 只转债；后缀法分不清 stock/etf）。"""
        astock = engine._sm_universe("stock")
        assert astock and all(v.endswith((".SZSE", ".SHSE", ".BSE")) for v, _u in astock)
        # venue 收窄：crypto 两所共享 bar_1d ⇒ 必须按 exchange 区分
        binance = engine._sm_universe("perp", "BINANCE")
        okx = engine._sm_universe("perp", "OKX")
        assert all(v.endswith(".BINANCE") for v, _u in binance)
        assert all(v.endswith(".OKX") for v, _u in okx)


# ---------------------------------------------------------------------------
# 2：§5.4 不确定门（抑制一切缺口主张）
# ---------------------------------------------------------------------------


class TestUncertainGate:
    @needs_db
    def test_inception_unknown_suppresses_claims(self):
        """裁定 F：真上币日不可得 ⇒ 落 `uncertain(inception_unknown)`，**绝不主张缺口**。

        这是 Binance 的常驻形状（`fapi` 被墙 ⇒ `symbol_inception` 恒 `None`）——若实现退化成
        「用源可达性冒充 inception」，本测必红（那是把「拉不到」恶化为「不认为缺失」）。
        """
        ad = _FakeCrypto(inception=None)
        out, repulls, alerts = _run(ad, universe=[("BTC.BINANCE", datetime.now(timezone.utc))],
                                    local={"20261001", "20261002", "20261003", "20261004"},
                                    today_end=date(2026, 10, 4))
        assert out[SID]["uncertain"] == 1 and out[SID]["new_open"] == 0
        assert _rows(SID) == [("BTC.BINANCE", "2019-12-31", "2026-10-04", "uncertain",
                               "inception_unknown", 0)]
        assert repulls == [] and alerts == [], "不确定态下不得重拉、不得告警"

    @needs_db
    def test_stale_snapshot_marks_all_symbols_uncertain(self):
        """列表快照 age 超阈 ⇒ 期望集不完整 ⇒ `stale_source`（§九.1 依赖倒置的闭合口）。"""
        old = datetime.now(timezone.utc) - timedelta(days=engine._SNAPSHOT_STALE_DAYS + 1)
        ad = _FakeCrypto(inception="2026-10-01")
        out, _r, alerts = _run(ad, universe=[("BTC.BINANCE", old), ("ETH.BINANCE", old)],
                               local={"20261001"}, today_end=date(2026, 10, 4))
        assert out[SID]["stale_source"] is True and out[SID]["uncertain"] == 2
        assert {r[3] for r in _rows(SID)} == {"uncertain"}
        assert {r[4] for r in _rows(SID)} == {"stale_source"}
        assert alerts == []

    @needs_db
    def test_fresh_snapshot_with_known_inception_claims_gaps(self):
        """门槛之外的正常路径：inception 已知 ＋ 快照新鲜 ⇒ 差集落 `open` ＋ 聚合告警。"""
        ad = _FakeCrypto(inception="2026-10-01")     # 下界＝max(inception, src_lo)=2026-10-01
        out, _r, alerts = _run(ad, universe=[("BTC.BINANCE", datetime.now(timezone.utc))],
                               local={"20261001", "20261003", "20261004"},
                               today_end=date(2026, 10, 4))
        assert out[SID]["new_open"] == 1
        assert _rows(SID) == [("BTC.BINANCE", "2026-10-02", "2026-10-02", "open", None, 0)]
        assert alerts and alerts[0][0] == SID
        assert "BTC.BINANCE" in alerts[0][1][0]

    @needs_db
    def test_expected_lower_bound_uses_window_floors(self):
        """下界＝`_window_floors`（**同一真源**）：retention 晚于 inception 时由 retention 绑。"""
        ad = _FakeCrypto(inception="2026-01-01")
        out, _r, _a = _run(ad, universe=[("BTC.BINANCE", datetime.now(timezone.utc))],
                           local={"20261003"}, cfg_retention=date(2026, 10, 2),
                           today_end=date(2026, 10, 4))
        # 下界＝max(2026-01-01, retention 2026-10-02, 2019-12-31) = 2026-10-02
        # 期望日＝10-02..10-04；本地只有 10-03 ⇒ 缺口 10-02 与 10-04（不连续 ⇒ 两段）
        assert out[SID]["new_open"] == 2
        assert [(r[1], r[2], r[3]) for r in _rows(SID)] == [
            ("2026-10-02", "2026-10-02", "open"), ("2026-10-04", "2026-10-04", "open")]

    @needs_db
    def test_floor_after_window_end_is_noop(self):
        """新上币（下界晚于窗口上界）⇒ 本轮无作业，**不是**缺口（也不得落 uncertain）。"""
        ad = _FakeCrypto(inception="2026-12-01")
        out, _r, alerts = _run(ad, universe=[("NEW.BINANCE", datetime.now(timezone.utc))],
                               local=set(), today_end=date(2026, 10, 4))
        assert out[SID]["new_open"] == 0 and out[SID]["uncertain"] == 0
        assert _rows(SID) == [] and alerts == []


# ---------------------------------------------------------------------------
# 3：限频重拉与闭合
# ---------------------------------------------------------------------------


class TestLimitedRepull:
    @needs_db
    def test_list_repullable_frequency_rules(self):
        """候选判据＝`pull_count < _GAP_REPULL_MAX` **且** 距 `last_seen ≥ 1 天`（真库 SQL 行为）。"""
        sid = NS + "_rep"
        _exec("INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state, pull_count,"
              " last_seen) VALUES"
              " (%s,'A',DATE '2026-10-01',DATE '2026-10-01','open',0, now() - interval '2 days'),"
              " (%s,'B',DATE '2026-10-01',DATE '2026-10-01','open',9, now() - interval '2 days'),"
              " (%s,'C',DATE '2026-10-01',DATE '2026-10-01','open',0, now()),"
              " (%s,'D',DATE '2026-10-01',DATE '2026-10-01','closed',0, now() - interval '9 days')",
              (sid, sid, sid, sid))
        got = engine._list_repullable(sid, "A"), engine._list_repullable(sid, "B")
        assert got[0] == [("2026-10-01", "2026-10-01")], "A：达标候选"
        assert got[1] == [], "B：pull_count 超限 ⇒ 不再重拉（防死循环）"
        assert engine._list_repullable(sid, "C") == [], "C：刚试过 ⇒ 未到间隔"
        assert engine._list_repullable(sid, "D") == [], "D：终态不重拉"

    @needs_db
    def test_repull_closes_row_and_bumps_pull_count(self):
        """限频重拉成功 ⇒ 视图式同步自然闭合（`closed`），`pull_count` 递增（尝试计数）。"""
        sid = SID
        ad = _FakeCrypto(inception="2026-10-01")
        _exec("INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state, pull_count,"
              " last_seen) VALUES (%s,'BTC.BINANCE',DATE '2026-10-02',DATE '2026-10-02',"
              "'open',0, now() - interval '2 days')", (sid,))
        out, repulls, _a = _run(ad, universe=[("BTC.BINANCE", datetime.now(timezone.utc))],
                                local={"20261001", "20261002", "20261003", "20261004"},
                                today_end=date(2026, 10, 4))
        assert repulls == [("BTC", "2026-10-02", "2026-10-02")], "按缺口区间重拉（源符号形态）"
        assert out[SID]["repulled"] == 1 and out[SID]["closed"] == 1
        row = _rows(SID)[0]
        assert (row[1], row[2], row[3], row[5]) == ("2026-10-02", "2026-10-02", "closed", 1)

    @needs_db
    def test_repull_failure_keeps_row_open_and_counts(self):
        """重拉失败 ⇒ 行仍 `open`（**不谎报补齐**），且错误计入 stat（可见）。"""
        sid = SID
        ad = _FakeCrypto(inception="2026-10-01")
        _exec("INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state, pull_count,"
              " last_seen) VALUES (%s,'BTC.BINANCE',DATE '2026-10-02',DATE '2026-10-02',"
              "'open',0, now() - interval '2 days')", (sid,))
        out, _r, _a = _run(ad, universe=[("BTC.BINANCE", datetime.now(timezone.utc))],
                           local={"20261001", "20261003", "20261004"},
                           today_end=date(2026, 10, 4), repull_error=RuntimeError("代理不通"))
        assert out[SID]["errors"] == 1
        assert _rows(SID)[0][3] == "open"

    @needs_db
    def test_disabled_sync_skipped(self):
        """未启用/无配置的族直接跳过（不产生行、不报错）。"""
        with patch.object(engine, "_RECONCILE_SYMBOL_SCOPE",
                          {"nope": ("bar_daily", "perp", "1D", "bar_1d", "UTC", "perp")}), \
             patch.object(engine, "_get_config", return_value={}):
            out = engine._reconcile_symbols()
        assert out == {"nope": {"skipped": "未配置/已禁用"}}

    @needs_db
    def test_alert_only_for_new_open(self):
        """**只对本轮新 open 响铃**：既有 open（已在上轮告过）不重响——防告警风暴。"""
        sid = SID
        ad = _FakeCrypto(inception="2026-10-01")
        _exec("INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state, pull_count,"
              " last_seen) VALUES (%s,'BTC.BINANCE',DATE '2026-10-02',DATE '2026-10-02',"
              "'open',9, now())", (sid,))
        _out, _r, alerts = _run(ad, universe=[("BTC.BINANCE", datetime.now(timezone.utc))],
                                local={"20261001", "20261003", "20261004"},
                                today_end=date(2026, 10, 4))
        assert alerts == [], "同一缺口续存 ⇒ 不是新 open"
