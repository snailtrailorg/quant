"""批 70：M6 退役测试钉（删干净+告警翻转当日窗+pub 键单账户来源+perm 退役）。"""
from unittest.mock import MagicMock


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
        # 批 86-B：19 → 20（新增 paper-trade 入册，live 组 order 顺延）；批 88：20 → 19（routing 退册——页面降 tab，nav 维管不到）
        assert "trade-switch" not in ids and len(ids) == 20   # 批 171o：+emergency（19 前史见批86-B/88 注）

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
        _pub_write(r, "X.XSHG", _json.dumps({"last": 9.9}), 100.0, 1)   # 旧 ts
        r.set.assert_not_called()
        _pub_write(r, "X.XSHG", _json.dumps({"last": 10.1}), 300.0, 2)  # 新 ts
        r.set.assert_called_once()
        args = r.set.call_args
        assert "hub:latest_tick:pub:X.XSHG" == args.args[0]
        payload = _json.loads(args.args[1])
        assert payload["_ts_epoch"] == 300.0 and payload["account_id"] == 2
        _pub_write(r, "X.XSHG", "{}", None, 1)   # ts None 跳过
        assert r.set.call_count == 1

    def test_reader_pub_then_full_tick(self):
        """读链：pub（最小块）→ 按 account_id 回读 per-account 全量（五档）；陈旧键降级腾讯。"""
        import json as _json
        import time as _t
        from unittest.mock import patch
        from src.data_platform import stock_detail as sd
        now = _t.time()
        pub = _json.dumps({"last": 10.5, "_ts_epoch": now, "account_id": "md-xtp"})
        from datetime import datetime as _dt
        full = _json.dumps({"last": 10.5, "ts": _dt.now().astimezone().isoformat(),
                            "bid": [10.4, 10.3, 10.2, 10.1, 10.0], "ask": [10.5, 10.6, 10.7, 10.8, 10.9]})
        r = MagicMock(side_effect=lambda k: pub if k.endswith(":pub:600000.XSHG") else full)
        with patch.object(sd, "_r", return_value=MagicMock(get=r)), \
             patch("src.data_platform.market_snapshot.get_quote", return_value={"source": "tencent"}):
            q = sd._quote_block("600000.SH", "600000.XSHG")
        assert q["source"] == "hub" and q["bid"][0] == 10.4 and "_ts_epoch" not in q
        # 陈旧 pub（10-22 残留键）必须走腾讯，不得把历史价当实时
        r_stale = MagicMock(return_value=_json.dumps({"last": 9.0, "_ts_epoch": 1.0, "account_id": "md-xtp"}))
        with patch.object(sd, "_r", return_value=MagicMock(get=r_stale)), \
             patch("src.data_platform.market_snapshot.get_quote", return_value={"source": "tencent", "bid": [1.0]}) as gq:
            q2 = sd._quote_block("600000.SH", "600000.XSHG")
        assert q2["source"] == "tencent" and gq.called
