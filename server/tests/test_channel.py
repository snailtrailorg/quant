"""消息通道单测：MessageChannel 实现（mock httpx）+ 未注册 provider。"""
from unittest.mock import patch, MagicMock


def test_wechat_work_channel_send():
    from src.alert_notify.channel import WechatWorkChannel
    ch = WechatWorkChannel("http://fake/webhook")
    with patch("httpx.post") as m:
        m.return_value.status_code = 200
        assert ch.send("title", "body") is True
        m.assert_called_once()


def test_discord_channel_send():
    from src.alert_notify.channel import DiscordChannel
    ch = DiscordChannel("http://fake/discord")
    with patch("httpx.post") as m:
        m.return_value.status_code = 200
        assert ch.send("t", "b") is True


def test_server_chan_channel_send():
    from src.alert_notify.channel import ServerChanChannel
    ch = ServerChanChannel("fake-sckey")
    with patch("httpx.post") as m:
        m.return_value.status_code = 200
        assert ch.send("t", "b") is True


def test_channel_send_failure():
    """发送失败（httpx 异常）返回 False"""
    from src.alert_notify.channel import WechatWorkChannel
    ch = WechatWorkChannel("http://invalid")
    with patch("httpx.post", side_effect=Exception("network")):
        assert ch.send("t", "b") is False


def test_get_channel_unknown_provider():
    """未注册 provider 返回 None"""
    from src.alert_notify.channel import get_channel
    assert get_channel("unknown_channel_xyz") is None
