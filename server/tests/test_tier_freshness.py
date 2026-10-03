"""项 18 三档新表新鲜度检测测试。

测试目标：_check_tier_freshness() 函数 -- 一档 9 表 sync_log 新鲜度、二档 4 增量表游标
+ pool_data 任务心跳（盲审 A-2：6 非增量表无游标由心跳覆盖）、异常容错。
纯 mock 不连真实 PG/Valkey。
"""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta


# ── 辅助 ──

def _fresh_ts(days_ago=0):
    """返回 days_ago 天前的 datetime。"""
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _fresh_tier1_rows():
    return [(sid, _fresh_ts(0.04)) for sid in [
        "stk_limit_sync", "moneyflow_sync", "margin_detail_sync",
        "top_list_sync", "block_trade_sync", "cyq_perf_sync",
        "forecast_sync", "namechange_sync", "concept_sync",
    ]]


def _fresh_cursor_rows():
    return [(tbl, _fresh_ts(1).strftime("%Y%m%d")) for tbl in
            ("income", "balancesheet", "cashflow", "fina_indicator")]


def _enabled_rows(sids=None):
    """sync_config 里 enabled 的 id 集合（TRANSITION-0129：检测先读它过滤停采项）。

    默认返回全部 tier1（等价「全部启用」），与加 enabled 过滤前行为一致。
    传 sids 可构造「部分停采」场景（缺哪个即视为该 sync enabled=false）。
    """
    if sids is None:
        sids = [r[0] for r in _fresh_tier1_rows()]
    return [(s,) for s in sids]


# ── 测试 ──

class TestTierFreshness:

    def test_fresh_tier1_no_alert(self):
        """一档 9 表 + 二档游标 + pool_data 心跳全新鲜 -> 返回空列表。"""
        from src.scheduler.tasks import _check_tier_freshness
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.side_effect = [
            _enabled_rows(),          # ① enabled 集合（TRANSITION-0129 前置查询）
            _fresh_tier1_rows(),      # ② 一档 sync_log
            _fresh_cursor_rows(),     # ③ 二档游标
        ]
        conn.execute.return_value.fetchone.return_value = (_fresh_ts(0.04),)
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            result = _check_tier_freshness()
        assert result == []

    def test_stale_tier1_alert(self):
        """一档 1 表 sync 在 3 天前（超 48h）-> 返回该表条目。"""
        from src.scheduler.tasks import _check_tier_freshness
        stale_ts = _fresh_ts(3)  # 3 天前
        conn = MagicMock()
        conn.__enter__.return_value = conn
        tier1_rows = [r for r in _fresh_tier1_rows() if r[0] != "stk_limit_sync"]
        tier1_rows.append(("stk_limit_sync", stale_ts))
        conn.execute.return_value.fetchall.side_effect = [
            _enabled_rows(),          # ① enabled 集合（TRANSITION-0129 前置查询）
            tier1_rows,               # ② 一档 sync_log
            _fresh_cursor_rows(),     # ③ 二档游标
        ]
        conn.execute.return_value.fetchone.return_value = (_fresh_ts(0.04),)
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            result = _check_tier_freshness()
        assert len(result) == 1
        assert result[0]["sync_id"] == "stk_limit_sync"
        assert result[0]["kind"] == "tier1"
        assert result[0]["age_hours"] is not None and result[0]["age_hours"] > 48

    def test_tier2_cursor_frozen(self):
        """二档游标表空 + pool_data 无 done 行（从未同步）-> 返回 4 表 + 心跳条目。"""
        from src.scheduler.tasks import _check_tier_freshness
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.side_effect = [
            _enabled_rows(),          # ① enabled 集合（TRANSITION-0129 前置查询）
            _fresh_tier1_rows(),      # ② 一档 sync_log
            [],                       # ③ pool_data_cursor 空
        ]
        conn.execute.return_value.fetchone.return_value = None
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            result = _check_tier_freshness()
        # 二档 4 增量表从未同步 + pool_data 心跳缺失 = 5 条
        tier2 = [r for r in result if r["kind"] == "tier2"]
        assert len(tier2) == 5
        for r in tier2:
            assert r["last_ts"] is None
            assert r["age_hours"] is None

    def test_db_exception_returns_empty(self):
        """get_conn 抛异常 -> 返回空列表，不抛。"""
        from src.scheduler.tasks import _check_tier_freshness
        import src.data_platform.db as db
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            result = _check_tier_freshness()
        assert result == []

    def test_disabled_sync_excluded_from_stale(self):
        """TRANSITION-0129：已停采（enabled=false）的 tier1 sync 不进断流告警。

        场景=concept_sync 上游接口不可用已停采（0129），它永无 success 行；无 enabled
        过滤则永久 stale（语义噪音）。反证同组：enabled 且陈旧的 moneyflow_sync 仍须
        报（防「过滤过宽致静默」）。
        """
        from src.scheduler.tasks import _check_tier_freshness
        stale_ts = _fresh_ts(3)   # 3 天前 > 48h 阈值
        enabled = [s for s in [r[0] for r in _fresh_tier1_rows()] if s != "concept_sync"]
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.side_effect = [
            _enabled_rows(enabled),           # ① 不含 concept_sync（已停采）
            [("moneyflow_sync", stale_ts)],   # ② 仅 moneyflow 有 success 行（陈旧）
            _fresh_cursor_rows(),             # ③ 二档游标
        ]
        conn.execute.return_value.fetchone.return_value = (_fresh_ts(0.04),)
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            result = _check_tier_freshness()
        ids = {r["sync_id"] for r in result}
        assert "concept_sync" not in ids, "停采的 sync 仍被报 stale（enabled 过滤失效）"
        assert "moneyflow_sync" in ids, "enabled 且陈旧者未报（过滤过宽致静默）"

    def test_no_enabled_rows_fail_open(self):
        """fail-open：读不到任何 enabled 行 ⇒ 退回全量检查，不静默失效。

        对齐 _tier_alert_filter 的「告警宁可重复不可丢」立法——表空/异常环境下宁可
        全量误报，也不能让断流检测静默归零。
        """
        from src.scheduler.tasks import _check_tier_freshness
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.side_effect = [
            [],                       # ① enabled 集合为空（异常环境）
            [],                       # ② 一档 sync_log 空（无 success 行）
            _fresh_cursor_rows(),     # ③ 二档游标
        ]
        conn.execute.return_value.fetchone.return_value = (_fresh_ts(0.04),)
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            result = _check_tier_freshness()
        ids = {r["sync_id"] for r in result}
        assert "concept_sync" in ids, "enabled 空集时未退回全量口径（fail-open 失效）"
        assert len([r for r in result if r["kind"] == "tier1"]) == 9

# ── 状态翻转告警过滤（盲审遗留 2026-08-22）──

class _FakeR:
    """Valkey set 语义的最小假件（smembers/delete/sadd）。"""

    def __init__(self, initial=None):
        self.store = set(initial or ())

    def smembers(self, key):
        return self.store

    def delete(self, key):
        self.store = set()

    def sadd(self, key, *vals):
        self.store.update(vals)


class TestTierAlertFilter:

    def _stale(self, *sids):
        return [{"sync_id": s, "last_ts": None, "age_hours": None, "kind": "tier1"}
                for s in sids]

    def test_first_run_all_new(self):
        """首跑（无历史状态）-> 全部视为新增告警。"""
        from src.scheduler.tasks import _tier_alert_filter
        fake = _FakeR()
        with patch("redis.Redis.from_url", return_value=fake):
            new, recovered = _tier_alert_filter(self._stale("a", "b"))
        assert {t["sync_id"] for t in new} == {"a", "b"}
        assert recovered == set()
        assert fake.store == {"a", "b"}   # 状态已写入

    def test_persistent_stale_suppressed(self):
        """持续 stale（状态未变）-> 不再告警（原 1h 一跑每天 24 条噪音/表）。"""
        from src.scheduler.tasks import _tier_alert_filter
        fake = _FakeR({"a", "b"})
        with patch("redis.Redis.from_url", return_value=fake):
            new, recovered = _tier_alert_filter(self._stale("a", "b"))
        assert new == []
        assert recovered == set()

    def test_new_entry_alerts(self):
        """状态扩大 -> 仅新变 stale 的条目告警。"""
        from src.scheduler.tasks import _tier_alert_filter
        fake = _FakeR({"a"})
        with patch("redis.Redis.from_url", return_value=fake):
            new, recovered = _tier_alert_filter(self._stale("a", "b"))
        assert [t["sync_id"] for t in new] == ["b"]
        assert recovered == set()

    def test_recovery_reported_once(self):
        """部分恢复 -> 恢复集返回（调用方发一条恢复行）；再次全恢复 -> 空。"""
        from src.scheduler.tasks import _tier_alert_filter
        fake = _FakeR({"a", "b"})
        with patch("redis.Redis.from_url", return_value=fake):
            new, recovered = _tier_alert_filter(self._stale("a"))
        assert new == []
        assert recovered == {"b"}
        assert fake.store == {"a"}
        with patch("redis.Redis.from_url", return_value=fake):
            new, recovered = _tier_alert_filter([])
        assert new == []
        assert recovered == {"a"}   # 最后一个也恢复

    def test_valkey_down_fail_open(self):
        """Valkey 不可用 -> fail-open 全量报（告警宁可重复不可丢）。"""
        from src.scheduler.tasks import _tier_alert_filter
        with patch("redis.Redis.from_url", side_effect=ConnectionError("valkey down")):
            new, recovered = _tier_alert_filter(self._stale("a"))
        assert {t["sync_id"] for t in new} == {"a"}
        assert recovered == set()
