"""批13 · 钉钉接入单测。

覆盖：fetch_access_token 失败路径（凭证预检 fail-fast 依据）/send_text 的 robotCode=app_key
语义/route_key 推导含 app_key（P0）/空凭证端点拦截（A-P2-8）/runner 凭证预检 SystemExit（A-P0-3）。
"""
from unittest.mock import MagicMock, patch


class TestFetchToken:
    def test_missing_credentials(self):
        from src.im_bot.dingtalk import fetch_access_token
        token, err = fetch_access_token("", "")
        assert token == "" and "未配置" in err

    def test_http_error(self):
        import httpx
        from src.im_bot.dingtalk import fetch_access_token
        with patch("src.im_bot.dingtalk.httpx.post",
                   return_value=MagicMock(status_code=401, text='{"code":"invalid"}')):
            token, err = fetch_access_token("k", "s")
        assert token == "" and "401" in err

    def test_ok(self):
        from src.im_bot.dingtalk import fetch_access_token
        with patch("src.im_bot.dingtalk.httpx.post",
                   return_value=MagicMock(status_code=200, json=lambda: {"accessToken": "at123"})):
            token, err = fetch_access_token("k", "s")
        assert token == "at123" and err == ""


class TestProvider:
    def test_send_text_robotcode_equals_appkey(self):
        """robotCode=app_key（官方 SDK chatbot.py 三处同判——钉死语义防漂移）。"""
        from src.im_bot.dingtalk import DingtalkProvider
        with patch("src.im_bot.credentials.get_bot_credentials",
                   return_value={"app_key": "AK1", "app_secret": "S1"}), \
             patch("src.im_bot.dingtalk.fetch_access_token", return_value=("tok", "")) as _ft, \
             patch("src.im_bot.dingtalk.httpx.post", return_value=MagicMock(status_code=200)) as mp:
            ok = DingtalkProvider().send_text(7, "staff_1", "userid", "hello")
        assert ok
        body = mp.call_args.kwargs["json"]
        assert body["robotCode"] == "AK1" and body["userIds"] == ["staff_1"]

    def test_send_card_stub_false(self):
        from src.im_bot.dingtalk import DingtalkProvider
        assert DingtalkProvider().send_card(7, "x", "userid", {}) is False


class TestRouteKey:
    def test_app_key_derives_route(self):
        """P0（A-P0-2/B-P0-1）：钉钉 app_key 进名单——第二只 bot 不再撞空 route 唯一索引。"""
        from src.im_bot.routing import route_key_from
        assert route_key_from({"app_key": "ak9", "app_secret": "s"}) == "ak9"

    def test_wecom_bot_id_str_not_confused_with_pk(self):
        """企微凭证键 bot_id（str）与平台 bot 主键（int）不同物——推导只查 creds dict。"""
        from src.im_bot.routing import route_key_from
        assert route_key_from({"bot_id": "wb77", "secret": "s"}) == "wb77"

    def test_empty_creds_route_empty(self):
        from src.im_bot.routing import route_key_from
        assert route_key_from({}) == ""
        assert route_key_from(None) == ""


_IDENT = {"sub": "1", "username": "admin", "role": "admin"}


class TestCreateGuard:
    def test_admin_create_empty_secret_rejected(self):
        """A-P2-8：manual-only 平台 secret 必填（防空 bot 进 pool 退避循环）。"""
        from src.web_api.routes.im_bots import im_bots_create
        from src.web_api.models import IMBotCreateReq
        conn = MagicMock(); conn.__enter__.return_value = conn
        with patch("src.web_api.routes.im_bots.get_conn", return_value=conn):
            try:
                im_bots_create(IMBotCreateReq(provider="dingtalk", name="d1",
                                              credentials={"app_key": "ak"}), _IDENT)
                raise AssertionError("应拒")
            except Exception as e:
                assert "CREDENTIALS_INCOMPLETE" in str(getattr(e, "code", "")) or "凭证" in str(e)


class TestRunnerPreflight:
    def test_bad_credentials_system_exit(self):
        """A-P0-3：凭证预检失败=SystemExit（不让 SDK 进无限重连僵尸态）。"""
        import pytest
        from src.im_bot.runners import dingtalk as R
        with patch("sys.argv", ["x", "9"]), \
             patch("src.im_bot.credentials.get_bot_credentials",
                   return_value={"app_key": "k", "app_secret": "s"}), \
             patch("src.im_bot.dingtalk.fetch_access_token", return_value=("", "HTTP 401")):
            with pytest.raises(SystemExit):
                R.main()

    def test_missing_credentials_system_exit(self):
        import pytest
        from src.im_bot.runners import dingtalk as R
        with patch("sys.argv", ["x", "9"]), \
             patch("src.im_bot.credentials.get_bot_credentials", return_value={}):
            with pytest.raises(SystemExit):
                R.main()
