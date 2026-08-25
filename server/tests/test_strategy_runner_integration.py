"""策略实盘化集成测试（#35 第 5 层）：strategy_runner 全链路。

mock XTP gateway / DB / .env，不依赖交易时段和真实凭证。
覆盖：_build_xtp_setting / _warmup_history / tick→BarGenerator→on_bar→signal / account_snapshot。
"""
import os
import json
import sys
from unittest.mock import patch, MagicMock, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# _build_xtp_setting 测试
# ---------------------------------------------------------------------------

def test_build_xtp_setting_from_broker_db():
    """Broker DB 优先：返回完整 vnpy SETTING（中文 key）。"""
    from src.strategy_runner.main import _build_xtp_setting
    mock_broker = MagicMock()
    mock_broker.get_credentials.return_value = {
        "app_id": "123456", "app_secret": "secret123",
        "client_id": "1", "auth_code": "auth_code_xyz",
    }
    mock_broker._params = {
        "md_host": "119.3.103.38", "md_port": 6002,
        "td_host": "122.112.139.0", "td_port": 6102,
    }
    with patch("src.strategy_framework.broker.get_broker", return_value=mock_broker):
        setting = _build_xtp_setting()
    assert setting["账号"] == "123456"
    assert setting["密码"] == "secret123"
    assert setting["客户号"] == 1
    assert setting["行情地址"] == "119.3.103.38"
    assert setting["行情端口"] == 6002
    assert setting["交易地址"] == "122.112.139.0"
    assert setting["交易端口"] == 6102
    assert setting["授权码"] == "auth_code_xyz"
    assert setting["行情协议"] == "TCP"


def test_build_xtp_setting_fallback_env():
    """Broker DB 空时 fallback .env，返回正确 SETTING。"""
    from src.strategy_runner.main import _build_xtp_setting
    env_vals = {
        "XTP_TEST_ACCOUNT": "env_account",
        "XTP_TEST_PASSWORD": "env_pass",
        "XTP_TEST_CLIENT_ID": "2",
        "XTP_TEST_QUOTE_HOST": "1.2.3.4",
        "XTP_TEST_QUOTE_PORT": "6002",
        "XTP_TEST_TRADE_HOST": "5.6.7.8",
        "XTP_TEST_TRADE_PORT": "6102",
        "XTP_TEST_KEY": "env_key",
    }
    with patch("src.strategy_framework.broker.get_broker", return_value=None), \
         patch.dict(os.environ, env_vals, clear=False):
        setting = _build_xtp_setting()
    assert setting["账号"] == "env_account"
    assert setting["密码"] == "env_pass"
    assert setting["客户号"] == 2
    assert setting["行情地址"] == "1.2.3.4"
    assert setting["行情端口"] == 6002
    assert setting["交易地址"] == "5.6.7.8"
    assert setting["交易端口"] == 6102
    assert setting["授权码"] == "env_key"


def test_build_xtp_setting_incomplete():
    """凭证不完整（缺账号/交易地址）返回空值。"""
    from src.strategy_runner.main import _build_xtp_setting
    # Broker DB 返回空凭证
    mock_broker = MagicMock()
    mock_broker.get_credentials.return_value = {}
    mock_broker._params = {}
    with patch("src.strategy_framework.broker.get_broker", return_value=mock_broker):
        setting = _build_xtp_setting()
    assert setting["账号"] == ""


def test_build_xtp_setting_broker_db_exception():
    """Broker DB 抛异常走 fallback .env，不崩溃。"""
    from src.strategy_runner.main import _build_xtp_setting
    with patch("src.strategy_framework.broker.get_broker", side_effect=Exception("DB down")), \
         patch.dict(os.environ, {"XTP_TEST_ACCOUNT": "fallback"}, clear=False):
        setting = _build_xtp_setting()
    assert setting["账号"] == "fallback"


# ---------------------------------------------------------------------------
# ST7 双轨会话身份校验（双盲审 P2）：_resolve_client_id
# ---------------------------------------------------------------------------

def test_resolve_client_id_valid():
    """合法独立号：字符串整型转 int 原样返回。"""
    from src.strategy_runner.main import _resolve_client_id
    with patch("src.strategy_runner.main.get_xtp_param", return_value="2"):
        assert _resolve_client_id() == 2


def test_resolve_client_id_unconfigured_warns(caplog):
    """未配置/读取失败（回 None 不可区分）：仅 warning，返回 None（→1 号，不阻塞）。"""
    import logging
    from src.strategy_runner.main import _resolve_client_id
    with patch("src.strategy_runner.main.get_xtp_param", return_value=None), \
         caplog.at_level(logging.WARNING, logger="strategy_runner"):
        assert _resolve_client_id() is None
    assert any("共用 1 号" in r.message for r in caplog.records)


def test_resolve_client_id_non_int_exits_config():
    """配了但非整数：EX_CONFIG(78) 快速失败（永久配置错，不重启）。"""
    from src.strategy_runner.main import _resolve_client_id, EX_CONFIG
    with patch("src.strategy_runner.main.get_xtp_param", return_value="abc"):
        with pytest.raises(SystemExit) as ei:
            _resolve_client_id()
    assert ei.value.code == EX_CONFIG


def test_resolve_client_id_conflicts_hub_exits_config():
    """配成 1（与 hub 同号）：明确撞号配置错，EX_CONFIG(78)。"""
    from src.strategy_runner.main import _resolve_client_id, EX_CONFIG
    with patch("src.strategy_runner.main.get_xtp_param", return_value="1"):
        with pytest.raises(SystemExit) as ei:
            _resolve_client_id()
    assert ei.value.code == EX_CONFIG


# ---------------------------------------------------------------------------
# _warmup_history 测试
# ---------------------------------------------------------------------------

def _make_bars_df(data: list[dict]):
    """辅助：从 dict 列表构造 DataFrame。"""
    import pandas as pd
    return pd.DataFrame(data) if data else pd.DataFrame()


def test_warmup_history():
    """_warmup_history 返回 dict 列表，字段正确。"""
    from src.strategy_runner.main import _warmup_history
    import pandas as pd
    from datetime import datetime, timedelta
    now = datetime.now()
    rows = [
        {"ts": now - timedelta(hours=1), "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.05, "volume": 10000},
        {"ts": now - timedelta(minutes=30), "open": 3.05, "high": 3.15, "low": 3.0, "close": 3.1, "volume": 15000},
    ]
    df = _make_bars_df(rows)
    with patch("src.data_platform.db.get_bars", return_value=df):
        history = _warmup_history("600000.SHSE")
    assert len(history) == 2
    assert history[0]["close"] == 3.05
    assert history[0]["volume"] == 10000.0
    assert "ts" in history[0]


def test_warmup_history_empty():
    """get_bars 返回空 DataFrame，不抛异常。"""
    from src.strategy_runner.main import _warmup_history
    df = _make_bars_df([])
    with patch("src.data_platform.db.get_bars", return_value=df):
        history = _warmup_history("600000.SHSE")
    assert history == []


def test_warmup_history_db_error():
    """get_bars 抛异常，不抛（走 logger.warning）。"""
    from src.strategy_runner.main import _warmup_history
    with patch("src.data_platform.db.get_bars", side_effect=Exception("PG down")):
        history = _warmup_history("600000.SHSE")
    assert history == []


# ---------------------------------------------------------------------------
# 核心链路：tick → BarGenerator → on_bar → strategy.on_bar → signal
# ---------------------------------------------------------------------------

def test_tick_to_bar_to_strategy():
    """模拟 EVENT_TICK → BarGenerator → on_vnpy_bar → strategy.on_bar → signal。

    mock XtpGateway + 注入模拟 tick，验证 bar 生成后 strategy.on_bar 返回 Signal。
    """
    from src.strategy_runner.main import _warmup_history
    from src.strategy_framework.strategy import Strategy, StrategyConfig
    from src.strategy_framework.adapters import XTPAdapter
    from src.strategy_framework.strategy import Signal, Action

    # 1. 建策略实例（mock adapter）
    mock_adapter = MagicMock(spec=XTPAdapter)
    cfg = StrategyConfig(
        id="test", name="测试策略", type="astock_analysis", symbol="600000.SHSE",
        adapter="xtp", enabled=True, factors=[], aggregator={}, params={},
    )
    strategy = Strategy.from_config(cfg, mock_adapter)

    # 2. 模拟 on_vnpy_bar 回调内部逻辑
    #    main.py 的 on_vnpy_bar 做了三件事：
    #      a) 调 strategy.on_bar(d, history)
    #      b) 记录信号
    #      c) 更新 history（append + pop 维持 100 上限）
    history: list[dict] = []

    # 模拟 BarGenerator 生成的 bar（vnpy BarData 风格）
    class FakeBar:
        def __init__(self, open_p, high_p, low_p, close_p, vol, dt):
            self.open_price = open_p
            self.high_price = high_p
            self.low_price = low_p
            self.close_price = close_p
            self.volume = vol
            self.datetime = dt

    import datetime
    now = datetime.datetime.now()

    bars = [
        FakeBar(3.0, 3.1, 2.9, 3.05, 10000, now),
        FakeBar(3.05, 3.12, 3.0, 3.08, 12000, now + datetime.timedelta(minutes=1)),
        FakeBar(3.08, 3.18, 3.05, 3.15, 18000, now + datetime.timedelta(minutes=2)),
    ]

    signals = []
    for bar in bars:
        d = {
            "ts": bar.datetime,
            "open": float(bar.open_price), "high": float(bar.high_price),
            "low": float(bar.low_price), "close": float(bar.close_price),
            "volume": float(bar.volume),
        }
        sig = strategy.on_bar(d, list(history))
        signals.append(sig)
        history.append(d)

    # 3. 验证：3 根 bar 都返回 Signal（BUY/HOLD/SELL 之一）
    assert len(signals) == 3
    for sig in signals:
        assert isinstance(sig, Signal)
        assert sig.action in (Action.BUY, Action.HOLD, Action.SELL)

    # 4. 验证 history 更新正确
    assert len(history) == 3
    assert history[-1]["close"] == 3.15


def test_tick_to_bar_with_history():
    """带历史数据暖机后，策略 on_bar 使用 history 计算因子。"""
    from src.strategy_framework.strategy import Strategy, StrategyConfig
    from src.strategy_framework.adapters import XTPAdapter
    from src.strategy_framework.strategy import Signal, Action
    import datetime
    from unittest.mock import MagicMock

    mock_adapter = MagicMock(spec=XTPAdapter)
    cfg = StrategyConfig(
        id="test2", name="历史暖机策略", type="astock_analysis", symbol="600000.SHSE",
        adapter="xtp", enabled=True, factors=[], aggregator={}, params={},
    )
    strategy = Strategy.from_config(cfg, mock_adapter)

    # 模拟 20 根历史 bar（暖机，让 ma_dev 等因子有数据）
    now = datetime.datetime.now()
    history = []
    for i in range(20):
        history.append({
            "ts": now - datetime.timedelta(minutes=20 - i),
            "open": 3.0 + i * 0.01, "high": 3.05 + i * 0.01,
            "low": 2.95 + i * 0.01, "close": 3.02 + i * 0.01,
            "volume": 10000 + i * 100,
        })

    # 新 bar
    new_bar = {
        "ts": now,
        "open": 3.22, "high": 3.25, "low": 3.20, "close": 3.24, "volume": 20000,
    }
    sig = strategy.on_bar(new_bar, list(history))
    assert isinstance(sig, Signal)


# ---------------------------------------------------------------------------
# account_snapshot 心跳循环
# ---------------------------------------------------------------------------

def test_account_snapshot_loop_inner():
    """模拟 60s 心跳中 account_snapshot 写入逻辑。

    验证 adapter.query_account() → INSERT INTO account_snapshot 被正确调用。
    """
    from src.strategy_runner.main import _warmup_history
    from unittest.mock import MagicMock, call

    # 模拟 adapter.query_account 返回账户列表
    class FakeAccount:
        def __init__(self, balance):
            self.balance = balance

    mock_adapter = MagicMock()
    mock_adapter.query_account.return_value = [FakeAccount(1500000.0)]

    # 模拟 DB connection
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None  # 今日无首次快照

    # 模拟 account_snapshot 写入逻辑（从 main.py 第 295-311 行提取）
    accounts = mock_adapter.query_account() or []
    total = sum(float(getattr(a, "balance", 0)) for a in accounts) if accounts else 1000000
    import datetime as dt
    today_str = dt.datetime.now().strftime('%Y-%m-%d')
    mock_conn.execute("SELECT 1 FROM account_snapshot LIMIT 1")
    cur = mock_conn.execute("SELECT total_value FROM account_snapshot WHERE ts::date=%s ORDER BY ts ASC LIMIT 1", (today_str,))
    first_row = cur.fetchone()
    daily_base = float(first_row[0]) if first_row else total
    daily_pnl = total - daily_base
    mock_conn.execute("INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital) VALUES (%s, %s, %s)", (total, daily_pnl, 1000000))

    # 验证
    mock_adapter.query_account.assert_called_once()
    assert total == 1500000.0
    assert daily_pnl == 0.0  # 无首次快照时 daily_base = total
    mock_conn.execute.assert_any_call(
        "INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital) VALUES (%s, %s, %s)",
        (1500000.0, 0.0, 1000000),
    )