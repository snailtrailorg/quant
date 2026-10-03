"""SF1 长尾清尾单测（2026-09-03）：F-39/F-51/F-55/F-59 四项「仍存在」缺陷的修复锁。

- F-39 cancel_order 退化路径：剥前缀得纯 orderid，非纯数字放弃盲撤
- F-51 同步游标在未来锚回过去（base>now 时锚回 now-7天，让 croniter 算最近到点判断逾期）
- F-55 factor:recalc 多 worker 各记 last_seen，不删全局键（原单消费者抢删）
- F-59 verify_jwt 复用连接池 + Valkey 挂 fail-closed（401 非 500，原每请求建连且无保护 → 认证全瘫）
"""
from unittest.mock import patch, MagicMock

import pytest

from datetime import datetime, timedelta, timezone

import src.data_sync  # noqa: F401  预加载：patch("datetime.datetime") 期间首次 import 会触发 pandas 链，
# 而 datetime 已被 mock → pandas._libs.interval 类型检查崩（patch sync 首次 import 的坑）


# --- F-39 cancel_order 退化路径 ---

def _adapter():
    from src.strategy_framework.adapters import XTPAdapter
    gw = MagicMock()
    gw.event_engine = None   # 跳过事件注册（无需 vnpy EventEngine）
    return XTPAdapter(gateway=gw), gw


def test_cancel_order_degenerate_strips_prefix():
    """无缓存退化：vt_orderid "XTP.123" → 剥前缀得纯 orderid "123"（原 int("XTP.123") 崩溃）。"""
    adapter, gw = _adapter()
    adapter.cancel_order("XTP.123")   # 无 _cid2vt/_orders 缓存，走退化
    req = gw.cancel_order.call_args.args[0]
    assert req.orderid == "123"
    assert req.symbol == ""            # symbol 未知（XTP cancel 实际只吃 orderid）


def test_cancel_order_degenerate_client_id_aborts():
    """client_id 非纯数字无法还原 orderid → 放弃盲撤（盲撤错单比不撤更危险）。"""
    adapter, gw = _adapter()
    adapter.cancel_order("1:123c1")
    gw.cancel_order.assert_not_called()


# --- F-51 游标在未来钳制 ---

def test_sync_scheduler_clamps_future_cursor():
    """last_sync_ts 在未来时 base 钳回 now——原未来 base 使 next_run 恒未来 → 永久停摆。"""
    from src.scheduler import tasks
    TZ_CN = timezone(timedelta(hours=8))
    now = datetime.now(TZ_CN)
    future = now + timedelta(days=10)   # 未来游标（aware 北京）

    fake_row = ("astock_daily", "30 16 * * 1-5", True, "idle", "20260810", future, "none")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [fake_row]

    captured = {}

    class _Cron:
        def __init__(self, schedule, base):
            captured["base"] = base

        def get_next(self, start):   # start 是 datetime 类（croniter 契约 get_next(datetime)）
            return datetime.now() + timedelta(days=1)   # 未来 → skip（不触发 sync）

    # 注意 patch 路径：get_conn 是 data_sync_scheduler 函数内 from src.data_platform.db import
    # 的，必须 patch src.data_platform.db.get_conn（patch tasks.get_conn 无效=假绿）
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch("croniter.croniter", _Cron):
        tasks.data_sync_scheduler()

    # base 被钳回过去（now-7天，等同空游标），让 croniter 算最近到点判断逾期
    assert abs((captured["base"] - (now - timedelta(days=7)).replace(tzinfo=None)).total_seconds()) < 60


# --- 批 80 游标卡非交易日（节假日落 cron 工作日 → next_run 恒停 → 同步卡死）---

def _sched_conn(row):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [row]
    return conn


def test_scheduler_rolls_next_run_to_trading_day():
    """游标停在节假日前一天，next_run 停非交易日 → 滚到下一个交易日触发 sync（批 80 根因）。"""
    from datetime import date
    from src.scheduler import tasks
    row = ("astock_daily", "30 16 * * 1-5", True, "idle", "20260924",
           datetime(2026, 9, 24, 16, 33), "trade_day")
    seq = iter([datetime(2026, 9, 25, 16, 30),   # 中秋周五（非交易日）
                datetime(2026, 9, 28, 16, 30)])  # 周一（交易日）

    class _Cron:
        def __init__(self, schedule, base):
            pass
        def get_next(self, start):
            return next(seq)

    def _td(d):
        return d == date(2026, 9, 28)   # 只有 09-28 是交易日

    fake_now = datetime(2026, 9, 29, 20, 0, tzinfo=timezone(timedelta(hours=8)))   # 钉死时钟
    with patch("datetime.datetime") as dt_mock:
        dt_mock.now.return_value = fake_now
        with patch("src.data_platform.db.get_conn", return_value=_sched_conn(row)), \
             patch("croniter.croniter", _Cron), \
             patch("src.data_platform.db.is_trading_day", _td), \
             patch("src.data_sync.sync") as s:
            tasks.data_sync_scheduler()
    s.assert_called_once_with("astock_daily")


def test_scheduler_rolls_next_run_to_workday():
    """workday 对称滚动：schedule 含周末，next_run 停周六 → 滚到周一（工作日）触发。"""
    from src.scheduler import tasks
    row = ("astock_daily", "0 9 * * *", True, "idle", "20260924",
           datetime(2026, 9, 24, 9, 0), "workday")   # 每天 9:00 + workday 过滤
    seq = iter([datetime(2026, 9, 26, 9, 0),   # 周六
                datetime(2026, 9, 27, 9, 0),   # 周日
                datetime(2026, 9, 28, 9, 0)])  # 周一

    class _Cron:
        def __init__(self, schedule, base):
            pass
        def get_next(self, start):
            return next(seq)

    fake_now = datetime(2026, 9, 29, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    with patch("datetime.datetime") as dt_mock:
        dt_mock.now.return_value = fake_now
        with patch("src.data_platform.db.get_conn", return_value=_sched_conn(row)), \
             patch("croniter.croniter", _Cron), \
             patch("src.data_sync.sync") as s:
            tasks.data_sync_scheduler()
    s.assert_called_once_with("astock_daily")   # 滚到周一（weekday<5）触发


def test_scheduler_breaks_when_next_trading_day_future():
    """今天=节假日，滚到下一个交易日（未来）时 break，不触发 sync。"""
    from datetime import date
    from src.scheduler import tasks
    row = ("astock_daily", "30 16 * * 1-5", True, "idle", "20260924",
           datetime(2026, 9, 24, 16, 33), "trade_day")
    seq = iter([datetime(2026, 9, 25, 16, 30),   # 中秋周五
                datetime(2026, 9, 28, 16, 30)])  # 周一（未来 > now=09-25）

    class _Cron:
        def __init__(self, schedule, base):
            pass
        def get_next(self, start):
            return next(seq)

    def _td(d):
        return d == date(2026, 9, 28)

    fake_now = datetime(2026, 9, 25, 16, 35, tzinfo=timezone(timedelta(hours=8)))
    with patch("datetime.datetime") as dt_mock:
        dt_mock.now.return_value = fake_now
        with patch("src.data_platform.db.get_conn", return_value=_sched_conn(row)), \
             patch("croniter.croniter", _Cron), \
             patch("src.data_platform.db.is_trading_day", _td), \
             patch("src.data_sync.sync") as s:
            tasks.data_sync_scheduler()
    s.assert_not_called()   # 下一个交易日 09-28 还没到


def test_scheduler_guard_exhausted_no_infinite_loop():
    """日历异常（is_trading_day 恒 False），guard 耗尽后判非交易日跳过，不死循环。"""
    from src.scheduler import tasks
    row = ("astock_daily", "30 16 * * 1-5", True, "idle", "20260924",
           datetime(2026, 9, 24, 16, 33), "trade_day")
    base = datetime(2026, 9, 24, 16, 30)
    cnt = [0]

    class _Cron:
        def __init__(self, schedule, b):
            pass
        def get_next(self, start):
            cnt[0] += 1
            return base + timedelta(days=cnt[0])   # 递增日期（全是非交易日）

    def _td(d):
        return False   # 恒非交易日

    fake_now = datetime(2027, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    with patch("datetime.datetime") as dt_mock:
        dt_mock.now.return_value = fake_now
        with patch("src.data_platform.db.get_conn", return_value=_sched_conn(row)), \
             patch("croniter.croniter", _Cron), \
             patch("src.data_platform.db.is_trading_day", _td), \
             patch("src.data_sync.sync") as s:
            tasks.data_sync_scheduler()
    s.assert_not_called()   # guard 耗尽后跳过，不触发 sync（不死循环）


# --- F-55 factor:recalc 多 worker 各记 last_seen ---

def test_recalc_hook_consumes_only_new_marker():
    """读到新标记才重算、不删全局键、同标记不重复消费（多 worker 各记 last_seen）。"""
    from src.strategy_runner import trading
    trading._recalc_seen = None   # 隔离：重置进程级状态
    r = MagicMock()
    r.get.return_value = "2026-09-03T10:00:00"
    rewarm = MagicMock()
    try:
        with patch("src.strategy_framework.factor.load_factors_from_db"):
            trading.recalc_hook(r, rewarm, [])
            assert rewarm.call_count == 1
            r.delete.assert_not_called()          # 不删全局键

            trading.recalc_hook(r, rewarm, [])    # 同标记 → 不重算
            assert rewarm.call_count == 1

            r.get.return_value = "2026-09-03T10:05:00"   # 新标记 → 重算
            trading.recalc_hook(r, rewarm, [])
            assert rewarm.call_count == 2
    finally:
        trading._recalc_seen = None


# --- F-59 verify_jwt Valkey 挂降级放行 ---

class _UConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        class _C:
            def fetchone(self):
                return (True, None, "viewer")   # enabled, deleted_at, role
        return _C()


def test_verify_jwt_valkey_down_fails_closed():
    """Valkey 挂（redis_client 抛异常）→ 黑名单检查 fail-closed（401 非 500，非放行）。

    放行会让已登出 token 复活（logout 无 PG 兜底）——fail-closed 才是安全正确降级。
    """
    from src.web_api import auth as auth_mod
    from fastapi import HTTPException
    token = auth_mod.create_jwt("1", "alice", "viewer")
    with patch("src.web_api.redis_pool.redis_client", side_effect=Exception("conn down")), \
         patch.object(auth_mod, "get_conn", lambda: _UConn()):
        with pytest.raises(HTTPException) as e:
            auth_mod.verify_jwt(token)
    assert e.value.status_code == 401   # fail-closed：Valkey 挂不放行


# --- F-40 query_account 断线清缓存 ---

def test_query_account_clears_cache_on_disconnect():
    """断线时 query_account 清缓存返回空（原返回陈旧值，污染 account_snapshot）。"""
    from src.strategy_framework.adapters import XTPAdapter
    gw = MagicMock()
    gw.event_engine = None
    adapter = XTPAdapter(gateway=gw)
    adapter._accounts["acct1"] = "stale"   # 预置上一轮陈旧缓存
    with patch.object(adapter, "_wait_update", return_value=False):   # 模拟超时（不真实 sleep）
        result = adapter.query_account()
    assert result == []   # 清缓存后断线返回空，snapshot_cycle 据此跳过不写假值


# --- F-56 worker TD 独立 client_id ---

def test_runner_client_id_derivation():
    """worker TD 独立 client_id：1-99 范围（XTP 普通用户），避开 hub MD 默认号。"""
    from src.strategy_framework.broker import runner_client_id
    assert runner_client_id(None) is None
    assert runner_client_id(1) == 2     # (1-1)%98+2
    assert runner_client_id(8) == 9
    # 范围锁：任意 task_id 都落在 2-99（普通用户 1-99，避开 hub MD 的 broker 默认号）
    for tid in range(1, 300):
        cid = runner_client_id(tid)
        assert 2 <= cid <= 99, f"task_id={tid} -> client_id={cid} 越界"


# --- OBS-1 调度器返回触发明细（2026-10-03 数据同步验证）---

def test_scheduler_returns_triggered_detail():
    """OBS-1：返回 triggered 明细（id + handler 返回值），不再被 len() 抹成数量。

    原 `{"triggered": len(triggered)}` 使生产日志只剩 `triggered: 2`——判不出发起的
    同步是被 SyncLock 秒回的 skipped 还是真发的 error（诊断重试风暴时为此绕道）。
    """
    from src.scheduler import tasks
    TZ_CN = timezone(timedelta(hours=8))
    # 刻意**不** patch datetime.datetime：croniter.get_next(datetime) 收的是真类，patch 后
    # 传入 MagicMock 会让其 issubclass 检查抛 TypeError（被 except 吞成「cron无效」→ 空触发）。
    # 改把游标设到 1 天前 + 高频 cron，让「到点」天然成立（时区无关）。
    past = (datetime.now(TZ_CN) - timedelta(days=1)).replace(tzinfo=None)
    row = ("astock_daily", "*/5 * * * *", True, "idle", "20260901", past, "none")
    with patch("src.data_platform.db.get_conn", return_value=_sched_conn(row)), \
         patch("src.data_sync.sync",
               return_value={"status": "error", "error": "boom"}) as s:
        r = tasks.data_sync_scheduler()
    assert r["n_triggered"] == 1
    assert r["triggered"] == [{"id": "astock_daily",
                               "result": {"status": "error", "error": "boom"}}]
    s.assert_called_once_with("astock_daily")


def test_scheduler_returns_async_dispatch_detail():
    """OBS-1：异步派发项明细标 dispatched=async（无内联 result）。"""
    from src.scheduler import tasks
    TZ_CN = timezone(timedelta(hours=8))
    past = (datetime.now(TZ_CN) - timedelta(days=1)).replace(tzinfo=None)
    row = ("pool_data", "*/5 * * * *", True, "idle", "20260901", past, "none")
    with patch("src.data_platform.db.get_conn", return_value=_sched_conn(row)), \
         patch.object(tasks.sync_via_celery, "apply_async") as ap:
        r = tasks.data_sync_scheduler()
    assert r["triggered"] == [{"id": "pool_data", "dispatched": "async"}]
    ap.assert_called_once()
