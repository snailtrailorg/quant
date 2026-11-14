"""批 171e · DEF-13 对账/补采路径加锁守门（`flow/任务/批171e-DEF13对账补采加锁.md` v3 §1.3）。

钉**路径可分辨**（171 纪律）：锁未获 ⇒ 跳过路径不产出补采循环的任何 per-symbol 行
（continuity）/不调 `_reconcile_symbols`（gap_reconcile）；获锁 ⇒ 行为零漂移。
"""
from unittest.mock import MagicMock, patch

from src.scheduler import tasks


def _fake_lock_cls(acquired: bool):
    class _FakeLock:
        def __init__(self, sync_id, ttl=60):
            self.sync_id = sync_id
            self.acquired = acquired

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _FakeLock


class TestContinuityLock:

    def _run(self, acquired=True, cnt=1):
        """mock 全外协跑 data_continuity_check（celery task .run 直调）。
        cnt：检测行计数。批 171k：cnt=0 断连=检测查询**无该标的行**+SM 全集含它
        （GROUP BY 只出有行标的——真断连标的在差集里，非 cnt=0 行）。"""
        def _exec(sql, *a):
            cur = MagicMock()
            if "security_master" in sql:
                cur.fetchall.return_value = [("600000.XSHG",)]
            else:
                cur.fetchall.return_value = (
                    [] if cnt == 0 else [("600000.XSHG", "2026-11-01", cnt)])
            return cur

        _redis_m = MagicMock()
        _redis_m.exists.return_value = False   # 批 171k：当日去重键不存在（首日语义）
        with patch("src.data_sync.sync_lock.SyncLock", _fake_lock_cls(acquired)), \
             patch("src.scheduler.tasks.business_redis", MagicMock(return_value=_redis_m)), \
             patch("src.scheduler.tasks.get_conn") as gc, \
             patch("src.data_platform.db.is_trading_day", return_value=True, create=True), \
             patch("src.data_platform.integrity.suspend_days_bulk", return_value={}, create=True), \
             patch("src.alert_notify.notify") as nf:
            gc.return_value.__enter__.return_value.execute.side_effect = _exec
            out = tasks.data_continuity_check.run()
        return out, nf

    def test_lock_not_acquired_skips_repair(self):
        """锁未获 ⇒ issues 含锁跳过词条且无 per-symbol 缺量行（路径分辨）+ repaired_bars==0。"""
        out, _nf = self._run(acquired=False)
        assert any("补采锁被持" in i for i in out["issues"]), "锁跳过词条在"
        assert not any("近7天仅" in i for i in out["issues"]), "跳过路径不产出缺量行"
        assert out["repaired_bars"] == 0

    def test_lock_acquired_reports_gap_and_repairs(self):
        """获锁 ⇒ cnt=0 断连 issue 照报 + 补采循环执行（pull_daily 被调）——零漂移直证。
        批 171k：缺量判定改 cnt=0 才报（原「近7天仅」词随阈值语义退役）——测试行 cnt=1
        在 171k 后属低频豁免象限，改 0 才触补采。"""
        with patch("src.data_platform.adapters.tushare_adapter.pull_daily",
                   return_value=MagicMock(empty=False)) as pl:
            out, _nf = self._run(acquired=True, cnt=0)
        assert any("整周零根" in i for i in out["issues"]), "cnt=0 断连行照报"
        pl.assert_called_once(), "补采循环执行"


class TestGapReconcileLock:

    def test_lock_not_acquired_returns_skipped_without_calling_engine(self):
        calls = []
        with patch("src.data_sync.sync_lock.SyncLock", _fake_lock_cls(False)), \
             patch("src.data_sync.engine._reconcile_symbols",
                   side_effect=lambda: calls.append(1)):
            out = tasks.data_gap_reconcile.run()
        assert out == {"status": "skipped", "reason": "上次对账仍在运行"}
        assert calls == [], "未获锁不得调 _reconcile_symbols"

    def test_lock_acquired_calls_engine_once(self):
        calls = []
        with patch("src.data_sync.sync_lock.SyncLock", _fake_lock_cls(True)), \
             patch("src.data_sync.engine._reconcile_symbols",
                   side_effect=lambda: calls.append(1) or {"ok": True}):
            out = tasks.data_gap_reconcile.run()
        assert calls == [1], "获锁恰调一次"
        assert out == {"reconcile": {"ok": True}}


class TestLockKeys:

    def test_key_literals_pinned(self):
        """锁键字面量钉（防键名漂移撞 sync_id 命名空间）——`sync:lock:` 前缀由 SyncLock 自带。"""
        import inspect
        src = inspect.getsource(tasks)
        assert 'SyncLock("reconcile:continuity")' in src
        assert 'SyncLock("reconcile:symbols")' in src
