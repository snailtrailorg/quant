"""批 63（二）行情网关插件 + 接口行选行测试钉（双盲审 P0-1/P1-1/P1-3/P2-1 回归钉）。"""
import json
from unittest.mock import MagicMock, patch

import pytest


def _conn(rows):
    """get_conn mock：with 语法 + execute 返回 fetchone。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchone.return_value = rows[0] if rows else None
    conn.execute.return_value = cur
    return conn


# ——— 网关注册表 / create_md_gateway ———

def test_list_md_gateway_providers_has_xtp():
    from src.strategy_framework.md_gateway import list_md_gateway_providers
    assert "xtp" in list_md_gateway_providers()


def test_create_md_gateway_unknown_provider_raises():
    from src.strategy_framework.md_gateway import create_md_gateway
    with pytest.raises(ValueError):
        create_md_gateway("no_such_provider", MagicMock())


# ——— xtp_setting_from 分支 ———

def test_xtp_setting_from_cred_path():
    from src.strategy_framework.broker import xtp_setting_from
    s = xtp_setting_from({"app_id": "A", "app_secret": "S", "auth_code": "K"},
                         {"md_host": "h", "md_port": 6001, "client_id": 7})
    assert s["账号"] == "A" and s["密码"] == "S" and s["客户号"] == 7
    assert s["行情地址"] == "h" and s["行情端口"] == 6001


def test_xtp_setting_from_env_fallback(monkeypatch):
    from src.strategy_framework.broker import xtp_setting_from
    monkeypatch.setenv("XTP_TEST_ACCOUNT", "envA")
    monkeypatch.setenv("XTP_TEST_QUOTE_HOST", "envh")
    s = xtp_setting_from({}, {})
    assert s["账号"] == "envA" and s["行情地址"] == "envh"


# ——— get_interface_row：P0-1/P2-1 回归钉 ———

def _xtp_row(cred_enc="ENC", params=None):
    return ("xtp", cred_enc, params or {}, "astock", ["trading", "quote"])


def test_get_interface_row_row_id_required():
    """批 66a 钉（D26 #6）：row_id 必填——缺省选行退役，None raise（无主路径静默绑首行=禁区）。"""
    from src.strategy_framework.broker import get_interface_row
    with pytest.raises(ValueError):
        get_interface_row(row_id=None)


def test_get_interface_row_missing_required_field_raises():
    """P0-1 回归：指定行凭证非空但缺 app_id（必填非密字段）→ raise，禁 .env fallback。"""
    from src.strategy_framework.broker import get_interface_row
    with patch("src.data_platform.db.get_conn",
               return_value=_conn([_xtp_row()])), \
         patch("src.quant_common.crypto.decrypt",
               return_value=json.dumps({"app_secret": "S"})):   # 缺 app_id
        with pytest.raises(RuntimeError):
            get_interface_row(row_id=1)


def test_get_interface_row_decrypt_failure_raises():
    """P2-1 回归：解密失败 raise（密钥错配 fail-fast），不吞成无凭证静默 .env。"""
    from src.strategy_framework.broker import get_interface_row
    with patch("src.data_platform.db.get_conn",
               return_value=_conn([_xtp_row()])), \
         patch("src.quant_common.crypto.decrypt", side_effect=Exception("bad key")):
        with pytest.raises(RuntimeError):
            get_interface_row(row_id=1)


def test_get_interface_row_complete_credentials_ok():
    """凭证完整（app_id+app_secret）→ 正常返回。"""
    from src.strategy_framework.broker import get_interface_row
    with patch("src.data_platform.db.get_conn",
               return_value=_conn([_xtp_row()])), \
         patch("src.quant_common.crypto.decrypt",
               return_value=json.dumps({"app_id": "A", "app_secret": "S"})):
        row = get_interface_row(row_id=1)
    assert row["provider"] == "xtp" and row["credentials"]["app_id"] == "A"



def _fake_emd_mod():
    """EMQ 绑定模块最小替身（QuoteApi/CreateQuoteApi/EMQ_LOG_LEVEL/_make_spi 兼容面）。"""
    from unittest.mock import MagicMock
    mod = MagicMock()
    api = MagicMock()
    api.Login.return_value = 0   # 默认登录成功
    mod.QuoteApi.CreateQuoteApi.return_value = api
    mod.QuoteApi._last_api = api
    import src.strategy_framework.md_gateway as gw
    from unittest.mock import patch
    # _make_spi 返回 None 即可（RegisterSpi MagicMock 吸收）
    return mod

# ——— 批 66b：EMQ 连接窗（staging 彩排实锤补齐——周日柜台 Login-1→78 锁死发布）———
class TestEmqWindow:
    def test_window_closed_on_weekend_defers_login(self, monkeypatch):
        """非交易日 connect=defer_login（不 Login 不 raise——api 已建待窗开沿）。"""
        from datetime import datetime
        from src.strategy_framework import md_gateway as gw
        monkeypatch.setattr(gw, "_load_emd_binding", lambda: _fake_emd_mod())
        monkeypatch.setattr(gw, "_make_spi", lambda m, s: None)
        monkeypatch.setattr("src.strategy_framework.md_session.is_trading_day", lambda d=None: False)
        g = gw.EmqMdGateway(None)
        g.connect({"emq_account": "a", "emq_password": "p"}, {"emq_l1_host": "1.2.3.4:8093"})
        assert g._connected is False and g._login_err == 0
        g._mod.QuoteApi._last_api.Login.assert_not_called()   # 未试登录=defer 钉

    def test_window_open_login_failure_raises(self, monkeypatch):
        """交易日窗内 Login 失败=真故障照旧 raise（窗不掩盖配置错/柜台真故障）。"""
        from datetime import datetime
        from src.strategy_framework import md_gateway as gw
        import pytest as _pytest
        mod = _fake_emd_mod()
        mod.QuoteApi._last_api.Login.return_value = -1
        monkeypatch.setattr(gw, "_load_emd_binding", lambda: mod)
        monkeypatch.setattr(gw, "_make_spi", lambda m, s: None)
        monkeypatch.setattr("src.strategy_framework.md_session.is_trading_day", lambda d=None: True)
        monkeypatch.setattr(gw, "_emq_window_open", lambda now, td: True)
        g = gw.EmqMdGateway(None)
        with _pytest.raises(RuntimeError):
            g.connect({"emq_account": "a", "emq_password": "p"}, {"emq_l1_host": "1.2.3.4:8093"})

    def test_window_open_edge_relogin_leg(self, monkeypatch):
        """窗开沿重登腿：defer 态+窗开 → poll_supervise 触发 _login_now（60s 节流）。"""
        from src.strategy_framework import md_gateway as gw
        monkeypatch.setattr(gw, "_load_emd_binding", lambda: _fake_emd_mod())
        monkeypatch.setattr(gw, "_make_spi", lambda m, s: None)
        monkeypatch.setattr("src.strategy_framework.md_session.is_trading_day", lambda d=None: False)
        g = gw.EmqMdGateway(None)
        g.connect({"emq_account": "a", "emq_password": "p"}, {"emq_l1_host": "1.2.3.4:8093"})
        monkeypatch.setattr(gw, "_emq_window_open", lambda now, td: True)   # 窗开模拟
        g.poll_supervise(in_session=True, trading_day=True)
        g._mod.QuoteApi._last_api.Login.assert_called_once()   # 重登腿触发

    def test_window_fn_semantics(self):
        from datetime import datetime
        from src.strategy_framework.md_gateway import _emq_window_open
        td = datetime(2026, 9, 28, 10, 0)      # 周一盘中
        assert _emq_window_open(td, True) is True
        assert _emq_window_open(datetime(2026, 9, 28, 7, 0), True) is False   # 早于 8:25
        assert _emq_window_open(datetime(2026, 9, 28, 16, 0), True) is False  # 晚于 15:15
        assert _emq_window_open(td, False) is False   # 非交易日全关
