"""批 70：M6 退役测试钉（删干净+告警翻转当日窗+pub 键单账户来源+perm 退役）。"""
from unittest.mock import MagicMock, patch


class TestRetirement:
    def test_engine_modules_gone(self):
        import importlib
        for mod in ("src.data_platform.trade_switch", "src.web_api.routes.trade_switch"):
            try:
                importlib.import_module(mod)
                raise AssertionError(f"{mod} 应已删除")
            except ModuleNotFoundError:
                pass

    def test_routes_not_registered(self):
        from src.web_api.main import app
        paths = {getattr(r, "path", "") for r in app.routes}
        assert not any("trade-switch" in p for p in paths)

    def test_nav_key_retired(self):
        from src.data_platform.perm_registry import NAV_ITEMS_BASE
        ids = {e["id"] for e in NAV_ITEMS_BASE}
        assert "trade-switch" not in ids and len(ids) == 19

    def test_routing_endpoint_no_column(self):
        """P0-2 钉：routing 端点不再 SELECT 已 drop 列。"""
        import inspect
        from src.web_api.routes import routing
        src = inspect.getsource(routing)
        assert "trade_switch_confirm_timeout_s" not in src.replace("批 70：trade_switch_confirm_timeout_s 列随", "RETIRED")


class TestUnfilledAlertFlip:
    def test_status_plus_day_window_sql(self):
        """P0-1 钉：当日窗谓词在（存量滞留单出窗）+status 判定（partial 计入）。"""
        import inspect
        from src.scheduler import tasks
        src = inspect.getsource(tasks)
        assert "status IN ('submitted','submitting','partial')" in src
        assert "AT TIME ZONE 'Asia/Shanghai'" in src


class TestPubLatestTick:
    def test_pub_write_epoch_guard(self):
        """pub 键写：新 epoch 覆盖/旧 epoch 不覆盖/ts None 跳过。"""
        from src.md_hub.parts import _pub_write
        import json as _json
        r = MagicMock()
        r.get.return_value = _json.dumps({"_ts_epoch": 200.0, "last": 10.0})
        _pub_write(r, "X.SHSE", _json.dumps({"last": 9.9}), 100.0, 1)   # 旧 ts
        r.set.assert_not_called()
        _pub_write(r, "X.SHSE", _json.dumps({"last": 10.1}), 300.0, 2)  # 新 ts
        r.set.assert_called_once()
        args = r.set.call_args
        assert "hub:latest_tick:pub:X.SHSE" == args.args[0]
        payload = _json.loads(args.args[1])
        assert payload["_ts_epoch"] == 300.0 and payload["account_id"] == 2
        _pub_write(r, "X.SHSE", "{}", None, 1)   # ts None 跳过
        assert r.set.call_count == 1

    def test_reader_single_get_pub(self):
        import json as _json
        from unittest.mock import patch
        from src.data_platform import stock_detail as sd
        r = MagicMock()
        r.get.return_value = _json.dumps({"last": 10.5, "_ts_epoch": 1.0, "account_id": 3, "ts": "2026-09-28T10:00:00+08:00"})
        with patch.object(sd, "_r", return_value=r), \
             patch("src.data_platform.market_snapshot.get_quote", return_value=None):
            q = sd._quote_block("600000.SH", "600000.SHSE")
        assert q["source"] == "hub" and q["last"] == 10.5 and "_ts_epoch" not in q
        r.get.assert_called_once_with("hub:latest_tick:pub:600000.SHSE")
