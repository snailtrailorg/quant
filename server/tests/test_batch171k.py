"""批 171k · 告警噪音治理四条守门（v2 规格 §1.4）。

①纯函数闸（cnt=0 四象限）②当日去重 ③cpu 晨窗豁免+键配置化 ④LLM empty 修实。
"""
from datetime import date
from unittest.mock import MagicMock, patch

from src.scheduler import tasks
from src.scheduler.tasks import _bar_gap_symbols


class TestBarGapPureFn:
    """A-P0-2：cnt=0 才算断连（66 标的分布四象限直证）。"""

    def test_four_quadrants(self):
        rows = {"A": 0, "B": 1, "C": 2, "D": 4}   # 实证分布的代表象限
        exp = {k: 5 for k in rows}
        gaps = _bar_gap_symbols(rows, exp)
        assert [g[0] for g in gaps] == ["A"], "仅 cnt=0 报（1/2/4 根全豁免）"
        assert gaps[0][2] == 5, "expected 透传"

    def test_empty_when_all_active(self):
        assert _bar_gap_symbols({"X": 3}, {"X": 5}) == []


class TestDailyDedup:
    """A-P1-1：bar 缺量段当日电平去重（同日吞/异日过/键不可用 fail-open）。"""

    def _run_scan(self, dedup_store=None):
        """mock 全外协跑 data_continuity_check（rows 含一只 cnt=0 标的）。"""
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False
        conn.execute.return_value.fetchall.return_value = [("600000.XSHG", None, 0)]
        redis_mock = MagicMock()
        redis_mock.exists.return_value = dedup_store is not None
        redis_mock.get.return_value = None
        notifies = []
        with patch("src.data_sync.sync_lock.SyncLock", _lock(True)), \
             patch.object(tasks, "get_conn", MagicMock(return_value=conn)), \
             patch.object(tasks, "business_redis", MagicMock(return_value=redis_mock)), \
             patch("src.alert_notify.notify", side_effect=lambda *a, **k: notifies.append(a)):
            out = tasks.data_continuity_check.run()
        return out, notifies

    def test_first_day_notifies(self):
        out, notifies = self._run_scan(dedup_store=None)
        assert any("600000.XSHG" in (n[3] or "") for n in notifies), "首日真断连照报"

    def test_same_day_swallowed(self):
        out, notifies = self._run_scan(dedup_store="1")   # 键存在=当日已发
        assert not any("600000.XSHG" in (n[3] or "") for n in notifies), "同日缺量被吞"


def _lock(acquired):
    class _L:
        def __init__(self, *a, **k):
            self.acquired = acquired

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return _L


class TestCpuMorningWindow:
    """晨窗豁免：窗内 warn 只记 info/窗外 warn 报/95% crit 恒报。"""

    def _eval(self, pct, now, window="08:00-09:30"):
        from src.health_monitor import monitor as M
        snap = {"resources": {"cpu": {"pct": pct}},
                "thresholds": {"cpu_morning_window": window} if window else {}}
        with patch.object(M, "datetime") as dt_mock:
            dt_mock.now.return_value.strftime.return_value = now
            out, _ = M.evaluate(snap, None)   # state=None 自建（传 mock 会 KeyError）
        return out

    def test_inside_window_warn_suppressed(self):
        out = self._eval(0.85, "08:30")
        assert not any(e["severity"] == "warning" for e in out), "窗内 warn 不报"
        assert any(e["severity"] == "info" for e in out), "窗内只记"

    def test_outside_window_warn_fires(self):
        out = self._eval(0.85, "10:00")
        assert any(e["severity"] == "warning" for e in out)

    def test_crit_always_fires(self):
        out = self._eval(0.95, "08:30")
        assert any(e["severity"] == "critical" for e in out), "crit 不豁免"

    def test_bad_window_fail_closed(self):
        out = self._eval(0.85, "08:30", window="not-a-window")
        assert any(e["severity"] == "warning" for e in out), "窗格式非法 ⇒ 不豁免"


class TestLlmEmptyDetection:
    """A-P1-2：content=='' ⇒ error+health.llm-degraded critical；PG 离线仍 iface-down。"""

    def test_empty_content_alerts(self):
        resp = MagicMock()
        resp.content = ""
        resp.usage = {}
        notifies = []
        caught = []
        with patch("src.llm_gateway.gateway.gateway") as gw, \
             patch.object(tasks, "business_redis", MagicMock()), \
             patch.object(tasks, "get_conn", MagicMock()), \
             patch("src.alert_notify.notify",
                   side_effect=lambda *a, **k: caught.append((a, k))):
            gw.chat.return_value = resp
            out = tasks.health_check.run()
        assert out["results"]["llm"]["status"] == "error", "空串=全挂信号"
        assert any(k.get("code") == "health.llm-degraded" for _a, k in caught), "新 code 告警"

    def test_pg_still_iface_down(self):
        """PG/Valkey 离线仍走 iface-down（errors 过滤排除 llm）。"""
        resp_ok = MagicMock()
        resp_ok.content = "pong"
        resp_ok.usage = {}
        notifies = []
        with patch("src.llm_gateway.gateway.gateway") as gw, \
             patch.object(tasks, "business_redis", MagicMock()) as br, \
             patch.object(tasks, "get_conn", MagicMock()), \
             patch("src.alert_notify.notify",
                   side_effect=lambda *a, **k: notifies.append((a, k))):
            gw.chat.return_value = resp_ok
            br.side_effect = RuntimeError("valkey down")   # Valkey 挂
            out = tasks.health_check.run()
        assert out["results"]["valkey"]["status"] == "error"
        assert any(k.get("code") == "health.iface-down" for _a, k in notifies)
