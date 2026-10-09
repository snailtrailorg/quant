"""批 114 · 冻结入口独立图标：`list_events(open_only=True)` SQL 过滤 + 全局端点 + 权限面。

桩模式：不连真 DB——mock `get_conn` 捕获 SQL 断言过滤位置（先过滤后截断）；端点直调
mock `freeze_event.list_events` 断言 open_only 透传 + limit 夹取；HTTP 层打桩 verify_jwt
断言 `read` 权限四角色可见（与铃铛 admin-only 面不同源）。

三条验收锚点（任务文件 §验收标准）：
1. `list_events(open_only=True)` 的 SQL 在 `LIMIT` **之前**追加 `unfrozen_at IS NULL`——
   「open_only 计数不被 limit 截断」反证（若先 LIMIT 50 再过滤，第 51 条起的未闭合行漏计）。
2. `GET /api/freeze-events?open_only=true` 端点返回 `{"events": [...]}` 且 open_only 透传。
3. 权限面 `read`：viewer/analyst/trader/admin 四角色皆可达（区别于铃铛 admin-only）。
"""
from unittest.mock import MagicMock, patch


def _conn(rows=()):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=rows))
    return conn


def _capture_sql(**list_kwargs):
    """跑一次 list_events，返回传给 get_conn 的 SQL 字符串与 args。"""
    from src.data_platform.freeze_event import list_events
    captured = {}
    conn = _conn()
    conn.execute.side_effect = lambda sql, args: (captured.update(sql=sql, args=args)
                                                  or MagicMock(fetchall=MagicMock(return_value=[])))
    # freeze_event 模块顶部 `from src.data_platform.db import get_conn`——本地名字，
    # 须 patch 模块命名空间里的符号，patch db.get_conn 不影响已绑定的本地引用。
    with patch("src.data_platform.freeze_event.get_conn", return_value=conn):
        list_events(**list_kwargs)
    return captured


class TestListEventsOpenOnlySql:
    def test_open_only_appends_unfrozen_filter(self):
        cap = _capture_sql(open_only=True)
        assert "unfrozen_at IS NULL" in cap["sql"]

    def test_open_only_filter_before_limit(self):
        """反证「open_only 计数不被 limit 截断」：过滤子句必须先于 LIMIT。

        若实现成「先 LIMIT 50 再筛未闭合」（子查询/外层过滤），则当未闭合行落在
        第 51 条之后时被截断漏计——角标数错误。故断言 `unfrozen_at IS NULL` 的位置
        在 `LIMIT` 之前（filter-then-truncate）。
        """
        cap = _capture_sql(open_only=True, limit=50)
        assert cap["sql"].index("unfrozen_at IS NULL") < cap["sql"].index("LIMIT"), \
            "过滤须在 LIMIT 之前（先过滤后截断），否则 open_only 计数被 limit 截断"

    def test_open_only_false_has_no_filter(self):
        cap = _capture_sql(open_only=False)
        assert "unfrozen_at IS NULL" not in cap["sql"]

    def test_open_only_combined_with_task_id_uses_and(self):
        """两个条件组合：task_id 已 `WHERE`，open_only 须 `AND` 拼接而非再 `WHERE`。"""
        cap = _capture_sql(task_id=7, open_only=True)
        assert "WHERE task_id=%s AND unfrozen_at IS NULL" in cap["sql"]
        assert tuple(cap["args"]) == (7, 50)   # task_id 值 + limit 值（open_only 无占位符）

    def test_open_only_alone_uses_where(self):
        """task_id=None 且 open_only=True 时须用 `WHERE`（无前置条件）。"""
        cap = _capture_sql(open_only=True)
        assert "WHERE unfrozen_at IS NULL" in cap["sql"]


class TestFreezeEventsGlobalEndpoint:
    def _call(self, **kw):
        from src.web_api.routes.trading import list_all_freeze_events
        return list_all_freeze_events(**kw)

    def test_returns_events_shape(self):
        with patch("src.data_platform.freeze_event.list_events",
                   return_value=[{"id": 1, "task_id": 5, "symbol": "600000.SH",
                                  "freeze_type": "ts_gap", "frozen_at": "2026-10-09T00:00:00+08:00"}]) as le:
            out = self._call(open_only=True, limit=50,
                             payload={"username": "admin", "db_role": "admin"})
        assert out["events"][0]["task_id"] == 5
        assert le.call_args.kwargs == {"task_id": None, "limit": 50, "open_only": True}

    def test_projects_minimal_field_set(self):
        """治理反证（步 4 P0-1 返工）：敏感字段不得投影给 read 面。

        list_events 返回全字段（含 watermark/gap_target_ts/account_id/operator），
        全局端点须只投影 task_id/symbol/freeze_type/frozen_at，其余一律不出现在响应里。
        若字段集被扩回敏感字段，本用例红。
        """
        full = [{"id": 1, "task_id": 5, "account_id": 99, "symbol": "600000.SH",
                 "freeze_type": "ts_gap", "frozen_at": "2026-10-09T00:00:00+08:00",
                 "watermark": "secret", "gap_target_ts": "secret2",
                 "unfrozen_at": None, "unfreeze_method": None, "operator": "admin"}]
        with patch("src.data_platform.freeze_event.list_events", return_value=full):
            out = self._call(open_only=True, limit=50, payload={"db_role": "admin"})
        ev = out["events"][0]
        assert set(ev.keys()) == {"id", "task_id", "symbol", "freeze_type", "frozen_at"}
        for sensitive in ("watermark", "gap_target_ts", "account_id", "operator",
                          "unfrozen_at", "unfreeze_method"):
            assert sensitive not in ev, f"敏感字段 {sensitive} 泄漏到 read 面"

    def test_limit_clamped(self):
        """limit 夹取 1..200（与 per-task 端点同语义）。"""
        with patch("src.data_platform.freeze_event.list_events", return_value=[]) as le:
            self._call(open_only=False, limit=9999, payload={"db_role": "admin"})
            assert le.call_args.kwargs["limit"] == 200
            self._call(open_only=False, limit=0, payload={"db_role": "admin"})
            assert le.call_args.kwargs["limit"] == 1

    def test_open_only_defaults_false(self):
        with patch("src.data_platform.freeze_event.list_events", return_value=[]) as le:
            self._call(limit=50, payload={"db_role": "admin"})
            assert le.call_args.kwargs["open_only"] is False


class TestFreezeEventsAuthGate:
    """HTTP 层：`read` 四角色可见（区别于铃铛 admin-only / per-task 端点的 mode 分档）。"""

    VIEWER = {"sub": "1", "username": "viewer", "role": "viewer", "db_role": "viewer"}
    ANALYST = {"sub": "2", "username": "analyst", "role": "analyst", "db_role": "analyst"}
    TRADER = {"sub": "3", "username": "trader", "role": "trader", "db_role": "trader"}
    ADMIN = {"sub": "4", "username": "admin", "role": "admin", "db_role": "admin"}

    def _get(self, who):
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        with patch("src.web_api.auth.verify_jwt", return_value=who), \
             patch("src.data_platform.freeze_event.list_events", return_value=[]):
            return TestClient(app).get("/api/freeze-events",
                                       headers={"Authorization": "Bearer t"})

    def test_viewer_allowed(self):
        assert self._get(self.VIEWER).status_code == 200

    def test_analyst_allowed(self):
        assert self._get(self.ANALYST).status_code == 200

    def test_trader_allowed(self):
        assert self._get(self.TRADER).status_code == 200

    def test_admin_allowed(self):
        assert self._get(self.ADMIN).status_code == 200
