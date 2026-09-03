"""SF1 长尾清尾单测（2026-09-03）：F-39/F-51/F-55/F-59 四项「仍存在」缺陷的修复锁。

- F-39 cancel_order 退化路径：剥前缀得纯 orderid，非纯数字放弃盲撤
- F-51 同步游标在未来钳制（base > now 时钳回 now，不再永久静默停摆）
- F-55 factor:recalc 多 worker 各记 last_seen，不删全局键（原单消费者抢删）
- F-59 verify_jwt 复用连接池 + Valkey 挂降级放行（原每请求建连且无保护 → 认证全瘫）
"""
from unittest.mock import patch, MagicMock

from datetime import datetime, timedelta, timezone


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

    # base 被钳回 now 附近（±60s），而非 future 的 +10 天
    assert abs((captured["base"] - now.replace(tzinfo=None)).total_seconds()) < 60


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


def test_verify_jwt_valkey_down_fails_open():
    """Valkey 挂（redis_client 抛异常）→ 跳过黑名单放行（不 500 认证全瘫）。"""
    from src.web_api import auth as auth_mod
    token = auth_mod.create_jwt("1", "alice", "viewer")
    with patch("src.web_api.redis_pool.redis_client", side_effect=Exception("conn down")), \
         patch.object(auth_mod, "get_conn", lambda: _UConn()):
        payload = auth_mod.verify_jwt(token)
    assert payload["jti"]           # 放行（黑名单未查，账号状态 PG 仍 fail-closed 兜底）


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
