"""SA4 重启策略升级测试（CrashLoopBackOff，2026-08-23）。

覆盖：
- 退出码分类：EX_TEMPFAIL(75)/EX_CONFIG(78) 语义 + main() 关键路径退出码
- _wait_for_deps：依赖就绪/退避耗尽/恢复中/喂看门狗
- _sa4_backoff_delay：指数退避 + 封顶
- sa4_reconciler：PG fail-safe / 用户已停只清状态 / 退避窗口 / 自动拉起 / 计数清零 / start 失败
纯 mock 不连真实 PG/Valkey/systemd。
"""
import subprocess
import time
from unittest.mock import patch, MagicMock, call

import pytest

from src.strategy_runner.main import EX_OK, EX_TEMPFAIL, EX_CONFIG


def _cp(returncode=0, stdout="", stderr=""):
    """仿 subprocess.run 结果。"""
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# ── 退出码常量 ──

class TestExitCodes:
    def test_constants(self):
        """sysexits 惯例：75=EX_TEMPFAIL（瞬态）、78=EX_CONFIG（永久）。"""
        assert EX_OK == 0
        assert EX_TEMPFAIL == 75
        assert EX_CONFIG == 78

    def test_main_deps_exhausted_tempfail(self, capsys):
        """探活退避耗尽 -> SystemExit(75)，走 _alert 不上抛。"""
        from src.strategy_runner import main as m
        with patch.object(m, "_wait_for_deps", return_value=False), \
             patch.object(m, "_alert") as p_alert, \
             patch.object(m, "MainEngine", MagicMock()), \
             patch("sys.argv", ["main", "--task-id", "8"]):
            with pytest.raises(SystemExit) as ei:
                m.main()
        assert ei.value.code == EX_TEMPFAIL
        p_alert.assert_called_once()

    def test_main_task_missing_config(self):
        """live_task 不存在（依赖就绪）-> SystemExit(78) 不重启。"""
        from src.strategy_runner import main as m
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn.execute.return_value = cur
        with patch.object(m, "_wait_for_deps", return_value=True), \
             patch.object(m, "MainEngine", MagicMock()), \
             patch("src.data_platform.db.get_conn", return_value=conn), \
             patch("sys.argv", ["main", "--task-id", "8"]):
            with pytest.raises(SystemExit) as ei:
                m.main()
        assert ei.value.code == EX_CONFIG

    def test_main_task_stopped_clean_exit(self):
        """任务已停止 -> SystemExit(0)（F-36：on-failure 不再拉起）。"""
        from src.strategy_runner import main as m
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = MagicMock()
        cur.fetchone.return_value = (8, "t", 1, "600000.SHSE", "{}", "{}", "stopped", None, None)
        conn.execute.return_value = cur
        with patch.object(m, "_wait_for_deps", return_value=True), \
             patch.object(m, "MainEngine", MagicMock()), \
             patch("src.data_platform.db.get_conn", return_value=conn), \
             patch("sys.argv", ["main", "--task-id", "8"]):
            with pytest.raises(SystemExit) as ei:
                m.main()
        assert ei.value.code == EX_OK


# ── 依赖探活 + 指数退避 ──

class TestWaitForDeps:
    def test_pg_ready_immediately(self):
        from src.strategy_runner import main as m
        with patch.object(m, "_pg_alive", return_value=True), \
             patch.object(m.time, "sleep") as p_sleep:
            assert m._wait_for_deps() is True
        p_sleep.assert_not_called()

    def test_backoff_sequence_and_watchdog(self):
        """PG 持续不可达：5->10->20->40s 退避序列，期间每轮喂看门狗。"""
        from src.strategy_runner import main as m
        with patch.object(m, "_pg_alive", return_value=False), \
             patch.object(m.time, "sleep") as p_sleep, \
             patch.object(m, "_sd_notify") as p_notify:
            assert m._wait_for_deps(max_wait=130) is False
        # max_wait=130：5+10+20+40=75（<130 继续），第 5 轮 waited=135>=130 耗尽不再睡
        assert [c.args[0] for c in p_sleep.call_args_list] == [5, 10, 20, 40]
        p_notify.assert_called_with("WATCHDOG=1")

    def test_pg_recovers_midway(self):
        """第 3 次探活恢复 -> True 且只 sleep 前 2 档。"""
        from src.strategy_runner import main as m
        with patch.object(m, "_pg_alive", side_effect=[False, False, True]), \
             patch.object(m.time, "sleep") as p_sleep:
            assert m._wait_for_deps() is True
        assert [c.args[0] for c in p_sleep.call_args_list] == [5, 10]

    def test_pg_alive_exception_false(self):
        from src.strategy_runner import main as m
        with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("down")):
            assert m._pg_alive() is False


# ── 退避计算 ──

class TestBackoffDelay:
    def test_exponential_with_cap(self):
        from src.scheduler.tasks import _sa4_backoff_delay
        assert _sa4_backoff_delay(0) == 0.0
        assert _sa4_backoff_delay(1) == 300.0
        assert _sa4_backoff_delay(2) == 600.0
        assert _sa4_backoff_delay(3) == 1200.0
        assert _sa4_backoff_delay(5) == 3600.0   # 4800 封顶
        assert _sa4_backoff_delay(10) == 3600.0


# ── reconciler ──

def _mk_conn(status=None):
    """get_conn mock：SELECT 1 通，live_task 查询返回 status 行（None=已删）。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    if status is None:
        cur.fetchone.return_value = None
    else:
        cur.fetchone.return_value = (status,)
    conn.execute.return_value = cur
    return conn


def _mk_valkey(counter=None):
    """Valkey mock：ping 通 + 可选退避计数 {attempts, ts}。"""
    r = MagicMock()
    data = dict(counter) if counter else {}
    r.hgetall.side_effect = lambda key: data.get(key, {})
    return r


class TestSa4Reconciler:
    def test_pg_down_fail_safe(self):
        """PG 不可达 -> 本轮整体跳过（fail-safe 不盲拉）。"""
        from src.scheduler import tasks as T
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.side_effect = RuntimeError("pg down")
        with patch.object(T, "_sa4_units", return_value=["quant-live-task@8.service"]), \
             patch.object(T, "get_conn", return_value=conn):
            result = T.sa4_reconciler()
        assert result["status"] == "skipped"

    def test_stopped_task_reset_only(self):
        """live_task=stopped（用户意图）-> 只 reset-failed 不 start。"""
        from src.scheduler import tasks as T
        conn = _mk_conn(status="stopped")
        with patch.object(T, "_sa4_units", side_effect=lambda s: {
                "failed": ["quant-live-task@8.service"], "active": []}[s]), \
             patch.object(T, "get_conn", return_value=conn), \
             patch.object(T, "_sa4_systemctl") as p_sys, \
             patch("redis.Redis.from_url", return_value=_mk_valkey()):
            result = T.sa4_reconciler()
        assert result["reset_only"] == ["quant-live-task@8.service"]
        p_sys.assert_called_once_with("reset-failed", "quant-live-task@8.service")

    def test_running_task_first_restart(self):
        """live_task=running 无计数 -> reset-failed + start，计数 attempts=1。"""
        from src.scheduler import tasks as T
        conn = _mk_conn(status="running")
        valkey = _mk_valkey()
        with patch.object(T, "_sa4_units", side_effect=lambda s: {
                "failed": ["quant-live-task@8.service"], "active": []}[s]), \
             patch.object(T, "get_conn", return_value=conn), \
             patch.object(T, "_sa4_systemctl", return_value=_cp()) as p_sys, \
             patch("redis.Redis.from_url", return_value=valkey), \
             patch("src.alert_notify.notify"):
            result = T.sa4_reconciler()
        assert result["restarted"] == ["quant-live-task@8.service"]
        assert p_sys.call_args_list == [
            call("reset-failed", "quant-live-task@8.service"),
            call("start", "quant-live-task@8.service"),
        ]
        key = "quant:sa4:backoff:quant-live-task@8.service"
        mapping = valkey.hset.call_args.kwargs["mapping"]
        assert mapping["attempts"] == 1

    def test_backoff_window_blocks_restart(self):
        """计数 attempts=1 且 ts=now（300s 窗口内）-> 跳过不拉起。"""
        from src.scheduler import tasks as T
        conn = _mk_conn(status="running")
        key = "quant:sa4:backoff:quant-live-task@8.service"
        valkey = _mk_valkey(counter={key: {"attempts": "1", "ts": str(time.time())}})
        with patch.object(T, "_sa4_units", side_effect=lambda s: {
                "failed": ["quant-live-task@8.service"], "active": []}[s]), \
             patch.object(T, "get_conn", return_value=conn), \
             patch.object(T, "_sa4_systemctl") as p_sys, \
             patch("redis.Redis.from_url", return_value=valkey):
            result = T.sa4_reconciler()
        assert result["restarted"] == []
        assert "quant-live-task@8.service" in result["skipped"]
        p_sys.assert_not_called()

    def test_backoff_elapsed_restarts(self):
        """计数 attempts=2 且 ts=1 小时前（600s 窗口已过）-> 拉起且 attempts=3。"""
        from src.scheduler import tasks as T
        conn = _mk_conn(status="running")
        key = "quant:sa4:backoff:quant-live-task@8.service"
        valkey = _mk_valkey(counter={key: {"attempts": "2", "ts": str(time.time() - 3600)}})
        with patch.object(T, "_sa4_units", side_effect=lambda s: {
                "failed": ["quant-live-task@8.service"], "active": []}[s]), \
             patch.object(T, "get_conn", return_value=conn), \
             patch.object(T, "_sa4_systemctl", return_value=_cp()) as p_sys, \
             patch("redis.Redis.from_url", return_value=valkey), \
             patch("src.alert_notify.notify"):
            result = T.sa4_reconciler()
        assert result["restarted"] == ["quant-live-task@8.service"]
        assert valkey.hset.call_args.kwargs["mapping"]["attempts"] == 3

    def test_stable_active_clears_counter(self):
        """active 超 10min 的单元 -> 退避计数清零（短暂失败不累积惩罚）。"""
        from src.scheduler import tasks as T
        key = "quant:sa4:backoff:quant-live-task@8.service"
        valkey = _mk_valkey(counter={key: {"attempts": "3", "ts": str(time.time() - 3600)}})
        with patch.object(T, "_sa4_units", side_effect=lambda s: {
                "failed": [], "active": ["quant-live-task@8.service"]}[s]), \
             patch.object(T, "get_conn", return_value=_mk_conn(status="running")), \
             patch("redis.Redis.from_url", return_value=valkey):
            result = T.sa4_reconciler()
        assert result["failed"] == 0
        valkey.delete.assert_called_once_with(key)

    def test_start_failure_no_counter_write(self):
        """systemctl start 失败 -> 记 skipped 不写计数（下轮重试同档位）。"""
        from src.scheduler import tasks as T
        conn = _mk_conn(status="running")
        valkey = _mk_valkey()

        def _sys(*args):
            return _cp(returncode=0) if args[0] == "reset-failed" else _cp(returncode=1, stderr="boom")

        with patch.object(T, "_sa4_units", side_effect=lambda s: {
                "failed": ["quant-live-task@8.service"], "active": []}[s]), \
             patch.object(T, "get_conn", return_value=conn), \
             patch.object(T, "_sa4_systemctl", side_effect=_sys), \
             patch("redis.Redis.from_url", return_value=valkey):
            result = T.sa4_reconciler()
        assert result["restarted"] == []
        valkey.hset.assert_not_called()

    def test_units_parse(self):
        """list-units 输出解析：单元名取首列，空行过滤。"""
        from src.scheduler import tasks as T
        out = "quant-live-task@8.service loaded failed failed  Quant Live Task 8\n\n"
        with patch.object(T, "_sa4_systemctl", return_value=_cp(stdout=out)):
            assert T._sa4_units("failed") == ["quant-live-task@8.service"]

    def test_systemctl_error_no_units(self):
        """systemctl 采集失败 -> 空列表（D-F5：采集失败≠健康）。"""
        from src.scheduler import tasks as T
        with patch.object(T, "_sa4_systemctl", return_value=None):
            assert T._sa4_units("failed") == []
        with patch.object(T, "_sa4_systemctl", return_value=_cp(returncode=1)):
            assert T._sa4_units("failed") == []
