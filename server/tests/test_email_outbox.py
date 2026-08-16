"""发件箱指数退避单测：_backoff_seconds 曲线（1→2→4→8→16→30 分钟封顶）。"""
from src.web_api.email_service import _backoff_seconds, MAX_ATTEMPTS


def test_backoff_curve():
    assert _backoff_seconds(1) == 60        # 1 分钟
    assert _backoff_seconds(2) == 120       # 2 分钟
    assert _backoff_seconds(3) == 240       # 4 分钟
    assert _backoff_seconds(4) == 480       # 8 分钟
    assert _backoff_seconds(5) == 960       # 16 分钟


def test_backoff_cap_30min():
    assert _backoff_seconds(6) == 1800      # 30 分钟封顶
    assert _backoff_seconds(100) == 1800


def test_max_attempts():
    assert MAX_ATTEMPTS == 6


def test_smtp_config_db_only():
    """SMTP 仅读 DB（2026-08-14 弃 .env）：DB username 存在即用 DB 值，.env 不参与。"""
    import os
    from unittest.mock import patch, MagicMock
    from src.web_api.email_service import _smtp_config
    mock = MagicMock()
    mock.__enter__.return_value = mock
    cur = MagicMock()
    cur.fetchall.return_value = [("smtp_username", "dbuser@x.com"), ("smtp_password", "ENC"),
                                ("smtp_host", "db.host"), ("smtp_from", "db@from")]
    mock.execute.return_value = cur
    with patch("src.data_platform.db.get_conn", return_value=mock), \
         patch("src.web_api.crypto_utils.decrypt", return_value="dbpass"), \
         patch.dict(os.environ, {"SMTP_USERNAME": "envuser", "SMTP_HOST": "env.host"}, clear=False):
        # .env 有值也不参与（单一真相源）；auto→587 推断为 starttls
        assert _smtp_config() == ("db.host", 587, "starttls", "dbuser@x.com", "dbpass", "db@from")


def test_smtp_config_unconfigured():
    """DB 无配置 → None；未开 SMTP_DEV 时发送返回错误（走重试→failed→铃铛）。"""
    import os
    from unittest.mock import patch, MagicMock
    from src.web_api.email_service import _smtp_config, _send_email_sync
    mock = MagicMock()
    mock.__enter__.return_value = mock
    cur = MagicMock()
    cur.fetchall.return_value = []
    mock.execute.return_value = cur
    with patch("src.data_platform.db.get_conn", return_value=mock):
        assert _smtp_config() is None
        # 未配置且未开 DEV → 失败（错误描述）
        with patch.dict(os.environ, {"SMTP_DEV": ""}, clear=False):
            assert "未配置" in _send_email_sync("a@b.c", "s", "b")
        # 显式 DEV 打印模式 → 成功（本地开发）
        with patch.dict(os.environ, {"SMTP_DEV": "true"}, clear=False):
            assert _send_email_sync("a@b.c", "s", "b") is None


def test_smtp_port_465_uses_ssl():
    """465=隐式 SSL（SMTP_SSL，不 starttls）；587=STARTTLS。"""
    import os
    from unittest.mock import patch, MagicMock
    import smtplib
    from src.web_api.email_service import _send_email_sync

    conf = ("h", 465, "ssl", "u", "p", "f@x.com")
    with patch("src.web_api.email_service._smtp_config", return_value=conf), \
         patch("src.web_api.email_service.smtplib.SMTP_SSL") as ssl_cls, \
         patch("src.web_api.email_service.smtplib.SMTP") as plain_cls:
        _send_email_sync("a@b.c", "s", "<p>x</p>")
    ssl_cls.assert_called_once_with("h", 465, timeout=60)
    plain_cls.assert_not_called()
    srv = ssl_cls.return_value.__enter__.return_value
    srv.starttls.assert_not_called()   # SSL 口不再 starttls
    srv.login.assert_called_once()

    conf587 = ("h", 587, "starttls", "u", "p", "f@x.com")
    with patch("src.web_api.email_service._smtp_config", return_value=conf587), \
         patch("src.web_api.email_service.smtplib.SMTP_SSL") as ssl_cls2, \
         patch("src.web_api.email_service.smtplib.SMTP") as plain_cls2:
        _send_email_sync("a@b.c", "s", "<p>x</p>")
    plain_cls2.assert_called_once_with("h", 587, timeout=60)
    ssl_cls2.assert_not_called()
    srv2 = plain_cls2.return_value.__enter__.return_value
    srv2.starttls.assert_called_once()
