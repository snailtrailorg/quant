"""批 71 · 数据质量地基四件套测试。

件 1：validate_bar_quality 真 bug 修复（DataFrame 进出+per-symbol 断点+fail-soft）
件 2：cur_version seed 后不走缺省（mock DB）
件 3：_log_usage 双写（llm_usage+data_source_usage 独立 try）
件 4：ApiError params 端点级形状（BOT_QUOTA）
"""
from unittest.mock import MagicMock, patch

import pandas as pd


def _df(rows):
    cols = ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "pre_close"]
    return pd.DataFrame(rows, columns=cols)


# ——— 件 1：validate_bar_quality ———

class TestValidateBarQuality:
    def test_dataframe_in_issues_out(self):
        """真 bug 回归钉：DataFrame 进（旧引擎传 list 恒 AttributeError=自引入 0 次成功）。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([
            ("000001.SZ", "20260925", 10, 10.2, 9.9, 10.1, 1000, 10.0),
            ("000001.SZ", "20260926", 10.1, 10.3, 10.0, 10.2, 1100, 10.1),
        ]))
        assert q["valid"] is True and not q["issues"]
        assert q["clean_count"] == 2

    def test_dirty_data_issues(self):
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([
            ("000001.SZ", "20260925", 0, 0, 0, 0, 100, 10.0),        # ohlc=0
            ("000001.SZ", "20260925", 10, 10, 10, 10, 100, 10.0),     # 重复
            ("000002.SZ", "20260925", 15, 15, 15, 16, 0, 10.0),       # vol=0（停牌标记）
        ]))
        assert any("为 0" in i for i in q["issues"])
        assert any("重复" in i for i in q["issues"])
        assert any("成交量为 0" in i for i in q["issues"])

    def test_per_symbol_gap_not_cross_symbol(self):
        """per-symbol 断点（批 71 groupby 向量化）：跨标的日期跳变不算断点。"""
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([
            ("000001.SZ", "20260101", 10, 10, 10, 10, 100, 10.0),
            ("000002.SZ", "20260925", 10, 10, 10, 10, 100, 10.0),   # 换标的=组边界非断点
        ]))
        assert not any("时序断点" in i for i in q["issues"])

    def test_real_gap_within_symbol_detected(self):
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(_df([
            ("000001.SZ", "20260101", 10, 10, 10, 10, 100, 10.0),
            ("000001.SZ", "20260925", 10, 10, 10, 10, 100, 10.0),   # 同标的隔 8 个月=真断点
        ]))
        assert any("时序断点" in i for i in q["issues"])


class TestLogBarQualityFailSoft:
    def test_engine_fail_soft_and_one_line_per_issue(self, caplog):
        """fail-soft 钉（v2 #1 双同 P1）：校验器抛错只 warning 不炸调用方；
        issues 一行一条（[quality] 前缀）。"""
        import logging
        from src.data_sync import engine
        # 真脏数据路径（双盲 P2-3：零 patch——trade_date="bad" 经 pd.to_datetime 真抛
        # ValueError，走 _log_bar_quality 的 fail-soft 分支）
        with caplog.at_level(logging.WARNING, logger=engine.__name__):
            engine._log_bar_quality(_df([("x", "bad", 1, 1, 1, 1, 1, 1)]), "bar_daily")
        assert any("校验器异常（不阻入库）" in r.message for r in caplog.records)

    def test_engine_issues_logged_per_line(self, caplog):
        import logging
        from src.data_sync import engine
        with caplog.at_level(logging.WARNING, logger=engine.__name__):
            engine._log_bar_quality(_df([
                ("000001.SZ", "20260925", 0, 0, 0, 0, 100, 10.0),
                ("000001.SZ", "20260925", 10, 10, 10, 10, 100, 10.0),
            ]), "bar_daily")
        lines = [r.message for r in caplog.records if "[quality]" in r.message]
        assert len(lines) >= 2   # 一行一 issue（v2 #16）


# ——— 件 2：cur_version seed ———

class TestCurVersionSeeded:
    def test_bar_minute_reads_seeded_two(self):
        """seed 后不走缺省 1（mock DB 返回 seed 行）。"""
        from src.data_platform import store
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = ("2",)
        with patch("src.data_platform.store.get_conn", return_value=conn):   # patch 被导入处（store.py from .db import）
            assert store.Store().cur_version("bar_minute") == 2

    def test_missing_key_falls_back_one(self):
        from src.data_platform import store
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = None
        with patch("src.data_platform.store.get_conn", return_value=conn):   # patch 被导入处（双盲 P2-1：死 mock 修正）
            assert store.Store().cur_version("nonexistent_kind") == 1
            assert conn.execute.called   # mock 真被走到（防环境耦合碰巧绿）


# ——— 件 3：_log_usage 双写 ———

class TestLlmUsageDoubleWrite:
    @staticmethod
    def _gw():
        from src.llm_gateway.gateway import LLMGateway
        with patch.object(LLMGateway, "_load_models_from_db", return_value=[]), \
             patch.object(LLMGateway, "_load_failover_config",
                          return_value={"retry_wait_s": 2, "circuit_breaker": {"fail_threshold": 5, "pause_s": 300}}):
            return LLMGateway()

    def test_writes_both_tables(self):
        gw = self._gw()
        conn = MagicMock()
        conn.__enter__.return_value = conn
        with patch("src.data_platform.db.get_conn", return_value=conn):
            gw._log_usage("deepseek", "m", 10, 20, 300, True, None, "test")
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("INSERT INTO llm_usage" in s for s in sqls)
        assert any("INSERT INTO data_source_usage" in s for s in sqls)
        ds = [c for c in conn.execute.call_args_list if "data_source_usage" in c.args[0]][0]
        assert ds.args[1] == ("deepseek", "llm:chat", 1, True, 300)

    def test_llm_usage_fail_does_not_skip_ds_usage(self, caplog):
        """两段独立 try（v2 #13）：llm_usage 失败不连带跳过 data_source_usage。"""
        import logging
        gw = self._gw()
        calls = []

        def _conn():
            c = MagicMock()
            c.__enter__.return_value = c

            def _exec(sql, *a):
                calls.append(sql)
                if "llm_usage" in sql and sql.startswith("INSERT"):
                    raise RuntimeError("llm_usage 表炸了")
                return MagicMock()
            c.execute.side_effect = _exec
            return c
        with patch("src.data_platform.db.get_conn", side_effect=_conn), \
             caplog.at_level(logging.WARNING):
            gw._log_usage("glm", "m", 1, 2, 50, False, "timeout", "test")
        assert any("INSERT INTO data_source_usage" in s for s in calls)   # 独立性钉：ds 写真发生了


# ——— 件 4：ApiError params ———

class TestApiErrorParams:
    def test_bot_quota_response_carries_params(self, monkeypatch):
        """端点级形状：BOT_QUOTA 400 响应顶层带 params={"max": 配置值}。"""
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        from src.web_api import auth as _auth
        from src.web_api.routes import im_bots

        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (1,)   # n_user=1 ≥ uq=1

        with patch.object(_auth, "verify_jwt",
                          return_value={"sub": "1", "username": "u", "role": "trader"}), \
             patch.object(im_bots, "get_conn", lambda: conn), \
             patch.object(im_bots, "_quota_limits", return_value=(1, 10)):
            client = TestClient(app)
            client.headers.update({"Authorization": "Bearer t"})
            # dingtalk=manual 方式在册（飞书扫码唯一会在 quota 前被 ONBOARDING_INTERACTIVE_ONLY 拦）
            r = client.post("/api/my/im-bots", json={
                "provider": "dingtalk", "name": "t", "credentials": {"corp_id": "x"}})
        assert r.status_code == 400
        body = r.json()
        assert body["code"] == "BOT_QUOTA"
        assert body.get("params") == {"max": 1}

    def test_params_none_absent(self):
        from src.web_api.errors import ApiError
        e = ApiError(400, "X", "msg")
        assert e.params is None and e.extra is None
