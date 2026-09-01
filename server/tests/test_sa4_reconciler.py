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
    """Valkey mock：ping 通 + 可选退避计数 {attempts, ts}。

    注：exists 未配置 -> MagicMock 真值=1（租约在场）-> 批5 后 L3 对 hub 走让位跳过，
    存量测试（断言均针对 live-task）不受影响；需 hub 被拉起的新测用 _mk_valkey2。
    """
    r = MagicMock()
    data = dict(counter) if counter else {}
    r.hgetall.side_effect = lambda key: data.get(key, {})
    r.exists.side_effect = lambda key: 1   # 双盲审 P1 显式化：租约在场=hub 让位（原靠 MagicMock 真值巧合——test_stable 无 systemctl 打桩，巧合破防会静默真跑）
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
             patch.object(T, "_sa4_strategy_unit_files", return_value=[]), \
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
             patch.object(T, "_sa4_strategy_unit_files", return_value=[]), \
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
             patch.object(T, "_sa4_strategy_unit_files", return_value=[]), \
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
        from unittest.mock import patch as _p
        _sp = _p("src.scheduler.tasks._sa4_systemctl")
        _sp.return_value = None  # 双盲审 P2：防御性打桩（本测不关心 systemctl 调用）
        key = "quant:sa4:backoff:quant-live-task@8.service"
        valkey = _mk_valkey(counter={key: {"attempts": "3", "ts": str(time.time() - 3600)}})
        # mock 形状同步（批5）：_desired_units 会经 _sa4_strategy_unit_files 查 systemctl，
        # 打桩防测试真调 systemctl；语义断言不变
        with patch.object(T, "_sa4_units", side_effect=lambda s: {
                "failed": [], "active": ["quant-live-task@8.service"]}[s]), \
             patch.object(T, "_sa4_strategy_unit_files", return_value=[]), \
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


# ── 批5：L3 扩面——期望表三源归一 + md-hub 三重熔断 + failed 态区分（D1/D2 v2.1）──


def _mk_conn2(running_tids=(), linked_sids=()):
    """_desired_units 用 conn mock：按 SQL 前缀分流（running 查询 / strategy_id 关联查询）。

    reconciler 级测试同样可用（"SELECT 1" 走 linked cursor 不 raise）。
    """
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur_running = MagicMock()
    cur_running.fetchall.return_value = [(t,) for t in running_tids]
    cur_linked = MagicMock()
    cur_linked.fetchall.return_value = [(s,) for s in linked_sids]
    conn.execute.side_effect = lambda sql, *a: (
        cur_running if "status='running'" in sql else cur_linked)
    return conn


def _mk_valkey2(counter=None, exists=None):
    """批5 Valkey mock：exists 按 key 映射（默认 0=键不在场，hub 可正常拉）；hgetall 按计数。"""
    r = MagicMock()
    data = dict(counter) if counter else {}
    ex = dict(exists) if exists else {}
    r.hgetall.side_effect = lambda key: data.get(key, {})
    r.exists.side_effect = lambda key: ex.get(key, 0)
    r.set.return_value = True   # 告警去重键 SET NX 恒成功（未去重 -> notify 会发）
    return r


def _run_l3(failed=(), active=(), conn=None, valkey=None, valkey_error=False,
            sys_side_effect=None, sys_return=None, strategy_files=()):
    """L3 测试公共桩：返回 (result, p_sys, p_notify)。默认无 live-task 期望 -> 期望表=[hub]。"""
    import contextlib
    from src.scheduler import tasks as T
    p_sys = MagicMock()
    if sys_side_effect is not None:
        p_sys.side_effect = sys_side_effect
    elif sys_return is not None:
        p_sys.return_value = sys_return
    if conn is None:
        conn = _mk_conn(status="stopped")
    with contextlib.ExitStack() as st:
        st.enter_context(patch.object(T, "_sa4_units", side_effect=lambda s: {
            "failed": list(failed), "active": list(active)}[s]))
        st.enter_context(patch.object(T, "_sa4_strategy_unit_files", return_value=list(strategy_files)))
        st.enter_context(patch.object(T, "get_conn", return_value=conn))
        st.enter_context(patch.object(T, "_sa4_systemctl", p_sys))
        if valkey_error:
            st.enter_context(patch("redis.Redis.from_url", side_effect=RuntimeError("valkey down")))
        else:
            st.enter_context(patch("redis.Redis.from_url",
                                   return_value=valkey if valkey is not None else _mk_valkey2()))
        p_notify = st.enter_context(patch("src.alert_notify.notify"))
        result = T.sa4_reconciler()
    return result, p_sys, p_notify


class TestDesiredUnits:
    def test_three_sources_unified(self):
        """期望表三源归一：live_task running + strategy is-enabled（无关联）+ hub 常开末位。"""
        from src.scheduler import tasks as T
        conn = _mk_conn2(running_tids=[8], linked_sids=[3])

        def _sys(*args):
            if args[0] == "list-unit-files":
                return _cp(stdout="quant-strategy@3.service enabled enabled\n"
                                  "quant-strategy@5.service enabled enabled\n"
                                  "quant-strategy@9.service disabled disabled\n")
            if args[0] == "is-enabled" and args[1] == "quant-strategy@5.service":
                return _cp(stdout="enabled\n")
            return _cp(returncode=1, stdout="disabled\n")

        with patch.object(T, "_sa4_systemctl", side_effect=_sys):
            desired = T._desired_units(conn)
        assert desired == [
            ("quant-live-task@8.service", "live_task"),
            ("quant-strategy@5.service", "strategy"),   # enabled 且无 live_task 关联
            (T.SA4_HUB_UNIT, "builtin"),
        ]  # @3 关联被护栏排除、@9 is-enabled=disabled 不进表

    def test_strategy_db_enabled_row_not_pulled(self):
        """D2 v2：strategy_config enabled DB 行不作拉起依据——单元未 is-enabled 即不进期望表。"""
        from src.scheduler import tasks as T
        conn = _mk_conn2()
        with patch.object(T, "_sa4_strategy_unit_files",
                          return_value=["quant-strategy@9.service"]), \
             patch.object(T, "_sa4_systemctl", return_value=_cp(returncode=1, stdout="disabled\n")):
            desired = T._desired_units(conn)
        assert [u for u, _ in desired] == [T.SA4_HUB_UNIT]
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert not any("strategy_config" in s for s in sqls)  # 全程不读 enabled DB 行

    def test_strategy_linked_to_live_task_no_double_pull(self):
        """v2.1 去重护栏：is-enabled 与 live_task 关联并存 -> strategy 单元不进期望表（防双拉）。"""
        from src.scheduler import tasks as T
        conn = _mk_conn2(running_tids=[8], linked_sids=[5])
        with patch.object(T, "_sa4_strategy_unit_files",
                          return_value=["quant-strategy@5.service"]), \
             patch.object(T, "_sa4_systemctl", return_value=_cp(stdout="enabled\n")):
            desired = T._desired_units(conn)
        assert ("quant-strategy@5.service", "strategy") not in desired
        assert ("quant-live-task@8.service", "live_task") in desired  # 唯一承载


class TestL3HubReconcile:
    def test_drift_starts_hub(self):
        """hub 常开期望 + systemd 无实例 -> L3 拉起 + 退避计数写共键 attempts=1。"""
        from src.scheduler import tasks as T
        valkey = _mk_valkey2()
        result, p_sys, _ = _run_l3(valkey=valkey, sys_return=_cp())
        assert result.get("l3_restarted") == [T.SA4_HUB_UNIT]
        assert p_sys.call_args == call("start", T.SA4_HUB_UNIT)
        assert valkey.hset.call_args.kwargs["mapping"]["attempts"] == 1
        assert valkey.hset.call_args.args[0] == "quant:sa4:backoff:" + T.SA4_HUB_UNIT

    def test_hub_backoff_window_skips(self):
        """hub 退避与 L1 共键：attempts=1 且 300s 窗口内 -> l3_skipped 不拉不写计数。"""
        from src.scheduler import tasks as T
        key = "quant:sa4:backoff:" + T.SA4_HUB_UNIT
        valkey = _mk_valkey2(counter={key: {"attempts": "1", "ts": str(time.time())}})
        result, p_sys, _ = _run_l3(valkey=valkey)
        assert result.get("l3_skipped") == [T.SA4_HUB_UNIT]
        assert "l3_restarted" not in result
        p_sys.assert_not_called()

    def test_l3_start_failed_stderr_and_alert(self):
        """P2(G4 ④a): systemctl start 失败 -> l3_failed 含 stderr + 告警发出(原版静默丢弃)。"""
        from src.scheduler import tasks as T
        valkey = _mk_valkey2()
        result, _, p_notify = _run_l3(
            valkey=valkey,
            sys_return=_cp(returncode=1, stderr="Start request repeated too quickly"),
        )
        l3f = result.get("l3_failed", [])
        assert len(l3f) == 1 and T.SA4_HUB_UNIT in l3f[0]
        assert "repeated too quickly" in l3f[0]   # stderr 采集
        assert p_notify.called                     # 告警发出(原版零告警)
        assert "l3_restarted" not in result       # 未拉起成功

    def test_stable_clear_generalized_to_hub(self):
        """stable-clear 泛化（D1 v2 修）：hub 稳定 active 超 10min -> 共键计数被清。"""
        from src.scheduler import tasks as T
        key = "quant:sa4:backoff:" + T.SA4_HUB_UNIT
        valkey = _mk_valkey2(counter={key: {"attempts": "3", "ts": str(time.time() - 3600)}})
        result, p_sys, _ = _run_l3(active=[T.SA4_HUB_UNIT], valkey=valkey)
        valkey.delete.assert_called_once_with(key)
        p_sys.assert_not_called()   # 在场不拉

    def test_lease_held_skips_without_backoff_write(self):
        """租约残留（对端实例在场）-> 让位跳过且不写退避计数（正常让位不受惩罚）。"""
        from src.scheduler import tasks as T
        valkey = _mk_valkey2(exists={T.SA4_HUB_LEASE_KEY: 1})
        result, p_sys, _ = _run_l3(valkey=valkey, sys_return=_cp())
        assert result.get("l3_guards", {}).get(T.SA4_HUB_UNIT) == "lease-held"
        p_sys.assert_not_called()
        valkey.hset.assert_not_called()

    def test_maintenance_marker_skips_and_alerts(self):
        """维护标记在场 -> 跳过 + 告警（写去重键，人工维护窗不打扰）。"""
        from src.scheduler import tasks as T
        valkey = _mk_valkey2(exists={T.SA4_HUB_MAINT_KEY: 1})
        result, p_sys, p_notify = _run_l3(valkey=valkey)
        assert result.get("l3_guards", {}).get(T.SA4_HUB_UNIT) == "maintenance"
        p_sys.assert_not_called()
        p_notify.assert_called_once()
        valkey.set.assert_called_once()   # 告警去重键（SET NX EX）

    def test_valkey_down_fail_closed(self):
        """Valkey 不可达 -> fail-closed：hub 跳过不盲拉（防双实例破坏 fencing）+ 告警。"""
        from src.scheduler import tasks as T
        result, p_sys, p_notify = _run_l3(valkey_error=True, sys_return=_cp())
        assert result.get("l3_guards", {}).get(T.SA4_HUB_UNIT) == "valkey-down"
        p_sys.assert_not_called()
        p_notify.assert_called_once()

    def test_hub_active_not_pulled(self):
        """hub 在场（active）-> 期望已满足不拉（常开语义≠重复拉）。"""
        from src.scheduler import tasks as T
        result, p_sys, _ = _run_l3(active=[T.SA4_HUB_UNIT])
        p_sys.assert_not_called()
        assert "l3_restarted" not in result and "l3_guards" not in result


class TestL3FailedStates:
    def test_failed_78_skipped_manual(self):
        """hub failed + ExecMainStatus=78 -> 不拉 + 告警人工（D1 v2 P0-1：78 黑洞不自动拉）。"""
        from src.scheduler import tasks as T

        def _sys(*args):
            return _cp(stdout="78\n") if args[0] == "show" else _cp()

        result, p_sys, p_notify = _run_l3(failed=[T.SA4_HUB_UNIT], sys_side_effect=_sys)
        assert result.get("l3_config_failed") == [T.SA4_HUB_UNIT]
        assert not any(c.args[0] in ("start", "reset-failed") for c in p_sys.call_args_list)
        p_notify.assert_called_once()

    def test_failed_crash_reset_and_start(self):
        """hub failed + ExecMainStatus=1（崩溃/StartLimit 打穿）-> reset-failed + start + 计数。"""
        from src.scheduler import tasks as T

        def _sys(*args):
            return _cp(stdout="1\n") if args[0] == "show" else _cp()

        valkey = _mk_valkey2()
        result, p_sys, _ = _run_l3(failed=[T.SA4_HUB_UNIT], sys_side_effect=_sys, valkey=valkey)
        assert result.get("l3_restarted") == [T.SA4_HUB_UNIT]
        assert p_sys.call_args_list == [
            call("show", T.SA4_HUB_UNIT, "--property=ExecMainStatus", "--value"),
            call("reset-failed", T.SA4_HUB_UNIT),
            call("start", T.SA4_HUB_UNIT),
        ]
        assert valkey.hset.call_args.kwargs["mapping"]["attempts"] == 1

    def test_l1_boundary_strategy_failed_routed_to_l3(self):
        """v2.1 职责边界：strategy failed 不走 L1（不查 live_task 意图），由 L3 拉起。"""
        from src.scheduler import tasks as T

        def _sys(*args):
            if args[0] == "is-enabled":
                return _cp(stdout="enabled\n")
            if args[0] == "show":
                return _cp(stdout="1\n")
            return _cp()

        conn = _mk_conn2()
        valkey = _mk_valkey2(exists={T.SA4_HUB_LEASE_KEY: 1})   # hub 让位，隔离断言
        result, p_sys, _ = _run_l3(failed=["quant-strategy@7.service"], conn=conn, valkey=valkey,
                                   sys_side_effect=_sys, strategy_files=["quant-strategy@7.service"])
        assert result.get("l3_restarted") == ["quant-strategy@7.service"]
        assert call("reset-failed", "quant-strategy@7.service") in p_sys.call_args_list
        assert call("start", "quant-strategy@7.service") in p_sys.call_args_list
        # L1 未接管：strategy 单元从未被当 live-task 查意图（职责边界实证）
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert "SELECT status FROM live_task WHERE id=%s" not in sqls

    def test_exec_status_parse(self):
        """ExecMainStatus 读取：正常解析 / 采集失败返回 None（按崩溃处理）。"""
        from src.scheduler import tasks as T
        with patch.object(T, "_sa4_systemctl", return_value=_cp(stdout="78\n")):
            assert T._sa4_exec_status("u.service") == 78
        with patch.object(T, "_sa4_systemctl", return_value=_cp(returncode=1)):
            assert T._sa4_exec_status("u.service") is None
        with patch.object(T, "_sa4_systemctl", return_value=_cp(stdout="\n")):
            assert T._sa4_exec_status("u.service") is None


class TestL3LiveTaskBoundary:
    """双盲审 B P1-1：L3 对 source=live_task 的 continue 分支回归锁。

    若该行被删：同周期 L1+L3 双拉 + attempts 每周期 +2 -> 退避指数加速翻倍 ->
    更长黑暗窗，且现有测试全绿（唯一无锁防线）。"""

    def test_l3_skips_live_task_failed_routed_to_l1(self):
        """running live-task 同在期望表与 failed 列表 -> L3 段跳过（归 L1），仅一组 start。"""
        from src.scheduler import tasks as T
        from unittest.mock import patch, MagicMock

        conn = MagicMock()
        conn.__enter__.return_value = conn

        def _exec(sql, params=None):
            cur = MagicMock()
            if "status='running'" in sql:
                cur.fetchall.return_value = [("8",)]        # 期望表：tid 8 running
            elif "SELECT status FROM live_task WHERE id" in sql:
                cur.fetchone.return_value = ("running",)     # L1 已停检查：仍 running
            elif "strategy_id" in sql:
                cur.fetchall.return_value = []
            return cur

        conn.execute.side_effect = _exec
        r = _mk_valkey()
        calls = []
        with patch.object(T, "_sa4_systemctl",
                          side_effect=lambda *a: calls.append(a) or MagicMock(returncode=0)) as _sc, \
             patch.object(T, "_sa4_units", side_effect=lambda st: {
                 "failed": ["quant-live-task@8.service"],
                 "active": [],
             }.get(st, [])), \
             patch.object(T, "get_conn", return_value=conn), \
             patch.object(T, "_sa4_strategy_unit_files", return_value=[]), \
             patch("redis.Redis.from_url", return_value=r), \
             patch("src.alert_notify.notify"):
            result = T.sa4_reconciler()
        starts = [a for a in calls if a and a[0] == "start"]
        resets = [a for a in calls if a and a[0] == "reset-failed"]
        # L1 恰一组（reset-failed + start live-task@8）；L3 对 live_task continue 不重复拉
        assert len(starts) == 1 and starts[0][1] == "quant-live-task@8.service"
        assert len(resets) == 1
