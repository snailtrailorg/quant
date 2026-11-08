"""批 171j · 污染检测+面板净化对照守门（v3 规格 §1.5）。

①品类口径+递变阈值（首日 info/递增 warn/递减不告）②risk API 净化对照三态契约
③beat 条目 ④runbook 词条在册。
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.scheduler import tasks
from src.data_sync import engine


# ---------- 件一：污染检测 ----------

class _FakeRedis:
    def __init__(self, prev=None):
        self.store = {"scan:pollution:prev_stock_null": str(prev)} if prev is not None else {}
        self.set_calls = []

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None):
        self.set_calls.append((k, v))
        self.store[k] = str(v)


def _lock(acquired=True):
    class _FakeLock:
        def __init__(self, sync_id, ttl=60):
            self.acquired = acquired

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return _FakeLock


def _scan(monkeypatch, rows, prev=None):
    """跑 data_pollution_scan。rows=[(category,nulls,total)]。返回 (result, fake_redis, notify_calls)。
    get_conn patch 目标=**tasks 命名空间**（顶层 :9 `from src.data_platform.db import get_conn`
    直绑定——patch 源头模块属性对已绑定名无效）。"""
    fr = _FakeRedis(prev=prev)
    notifies = []
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.execute.return_value.fetchall.return_value = rows
    with patch("src.data_sync.sync_lock.SyncLock", _lock(True)), \
         patch.object(tasks, "get_conn", MagicMock(return_value=conn)), \
         patch.object(tasks, "business_redis", MagicMock(return_value=fr)), \
         patch("src.alert_notify.notify", side_effect=lambda *a, **k: notifies.append(a)):
        out = tasks.data_pollution_scan.run()
    return out, fr, notifies


class TestPollutionScan:

    def test_category_counts_and_no_prev_info_only(self, monkeypatch):
        """首日（无前日键）：恒 info 计数不告警（B-P1-2 递变阈值）。"""
        rows = [("stock", 2925, 5566), ("listed_fund", 2191, 2980),
                ("convertible", 313, 1160), ("unknown", 3, 3)]
        out, fr, notifies = _scan(monkeypatch, rows, prev=None)
        assert out["stock_null"] == 2925 and out["fund_null"] == 2191
        assert out["cb_null"] == 313 and out["unknown_null"] == 3
        assert out["warned"] is False and out["prev_stock_null"] is None
        assert notifies == [], "首日无递变基准——不告"
        assert fr.set_calls and str(fr.set_calls[0][1]) == "2925", "前日键落 stock_null"

    def test_increase_warns(self, monkeypatch):
        """递增：2500→2925 ⇒ warn（attach 静默降级新信号）。"""
        rows = [("stock", 2925, 5566)]
        out, fr, notifies = _scan(monkeypatch, rows, prev=2500)
        assert out["warned"] is True
        assert notifies and notifies[0][0] == "warn"   # (level, kind, title, body, code=kw)

    def test_decrease_or_flat_no_warn(self, monkeypatch):
        """递减/持平不告（慢扫修数正常下降）。"""
        for prev, cur in [(3000, 2925), (2925, 2925), (2925, 0)]:
            out, _fr, notifies = _scan(monkeypatch, [("stock", cur, 5566)], prev=prev)
            assert out["warned"] is False, f"prev={prev} cur={cur} 不应告"
            assert notifies == []

    def test_lock_not_acquired_skips(self, monkeypatch):
        with patch("src.data_sync.sync_lock.SyncLock", _lock(False)):
            out = tasks.data_pollution_scan.run()
        assert out["status"] == "skipped"


# ---------- 件三：面板净化对照 ----------

class TestPanelPurifyColumns:

    def _api(self, monkeypatch, bar_rows, susp=None):
        """mock get_conn_for_heavy_read（:331 API 用 heavy 通道）+ integrity 函数源头
        （risk.py :375 函数内局部 import ⇒ patch src.data_platform.integrity 模块属性）。"""
        from contextlib import contextmanager
        susp = susp or {}
        conn = MagicMock()

        def _exec(sql, params=None):
            cur = MagicMock()
            if "trade_cal" in sql:
                from datetime import timedelta
                days, d = [], date(2026, 1, 1)
                while d <= date(2026, 12, 31):
                    if d.weekday() < 5:
                        days.append((d, True))
                    d += timedelta(days=1)
                cur.fetchall.return_value = days
            else:
                cur.fetchall.return_value = bar_rows
            return cur

        conn.execute.side_effect = _exec

        @contextmanager
        def _cm():
            yield conn

        import src.web_api.routes.risk as risk
        import src.data_platform.integrity as integ
        monkeypatch.setattr(risk, "get_conn_for_heavy_read", lambda *a, **k: _cm())
        monkeypatch.setattr(integ, "suspend_days_bulk", lambda f, l, s: susp)
        monkeypatch.setattr(integ, "delist_map_bulk", lambda: {})
        risk._AGG_CACHE.clear()
        return risk.data_integrity_api(freq="1D", payload={"username": "t"})

    def test_purified_symbol_columns(self, monkeypatch):
        """净化标的：suspended_days=len(_susp) 且 raw_expected>expected（扣停牌可见）。"""
        rows = [("600000.SHSE", 200, date(2026, 1, 1), date(2026, 12, 31))]
        susp = {"600000.SHSE": [date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 4)]}
        out = self._api(monkeypatch, rows, susp)
        item = out["items"][0]
        assert item["suspended_days"] == 3
        assert item["raw_expected"] > item["expected"], "对照列=未净化分母（更大）"
        assert out["summary"]["suspended_total"] == 3

    def test_crypto_and_missing_zero(self, monkeypatch):
        """三态契约：crypto（is_crypto 跳停牌）与 missing 行两列=0。"""
        rows = [
            ("BTCUSDT.BINANCE", 300, date(2026, 1, 1), date(2026, 12, 31)),  # crypto
            ("999999.SHSE", 0, None, None),                                    # missing
        ]
        out = self._api(monkeypatch, rows, {})
        btc = next(i for i in out["items"] if i["symbol"] == "BTCUSDT.BINANCE")
        miss = next(i for i in out["items"] if i["symbol"] == "999999.SHSE")
        assert btc["suspended_days"] == 0 and btc["raw_expected"] == btc["expected"]
        assert miss["suspended_days"] == 0 and miss["raw_expected"] == 0


# ---------- 件一基础设施 ----------

class TestInfrastructure:

    def test_beat_entry_exists(self):
        from src.scheduler.app import app as beat_app
        tasks_in_beat = {v["task"] for v in beat_app.conf.beat_schedule.values()}
        assert "src.scheduler.tasks.data_pollution_scan" in tasks_in_beat

    def test_runbook_entry(self):
        from src.alert_notify.runbook import RUNBOOK
        assert "data.pollution" in RUNBOOK

    def test_pollution_sql_sh_anchor(self):
        """日切片必须 AT TIME ZONE 'Asia/Shanghai'（A-P2-3——171d-C 纪律）。"""
        import inspect
        src = inspect.getsource(tasks.data_pollution_scan)
        assert "AT TIME ZONE 'Asia/Shanghai'" in src
