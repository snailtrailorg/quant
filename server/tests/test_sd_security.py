"""SD 安全批次测试（SD1/SD2，2026-08-17 稳定性检查 F-32/F-45/F-46/F-44/F-33）。

覆盖：
- F-45：禁用/注销用户 token 即时失效；users 不可读 fail-closed
- F-46：实盘模式下 admin 初始密码随机（不再 admin123）
- F-44：verify 无回测证据拒绝
- F-33：卡片时效（60s 窗口/无 ts 拒绝）
"""
from unittest.mock import MagicMock, patch

import pytest

import src.web_api.auth as auth_mod
from src.feishu_bot.bot import card_action_fresh, build_confirm_card


class TestVerifyJwtUserStatus:
    @staticmethod
    def _conn(row):
        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                class Cur:
                    def fetchone(self):
                        return row
                return Cur()
        return C()

    def test_disabled_user_rejected(self):
        token = auth_mod.create_jwt("1", "bob", "viewer")
        with patch.object(auth_mod, "get_conn", lambda: self._conn((False, None))):
            with pytest.raises(Exception) as e:
                auth_mod.verify_jwt(token)
            assert "禁用" in str(e.value.detail)

    def test_deleted_user_rejected(self):
        token = auth_mod.create_jwt("1", "bob", "viewer")
        with patch.object(auth_mod, "get_conn", lambda: self._conn((True, "2026-08-17"))):
            with pytest.raises(Exception) as e:
                auth_mod.verify_jwt(token)
            assert "注销" in str(e.value.detail)

    def test_unknown_user_rejected(self):
        token = auth_mod.create_jwt("1", "ghost", "viewer")
        with patch.object(auth_mod, "get_conn", lambda: self._conn(None)):
            with pytest.raises(Exception) as e:
                auth_mod.verify_jwt(token)

    def test_db_down_fail_closed(self):
        token = auth_mod.create_jwt("1", "bob", "viewer")

        class Boom:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                raise RuntimeError("PG 挂")

        with patch.object(auth_mod, "get_conn", lambda: Boom()):
            with pytest.raises(Exception) as e:
                auth_mod.verify_jwt(token)
            assert e.value.status_code == 401

    def test_enabled_user_passes(self):
        token = auth_mod.create_jwt("1", "bob", "viewer")
        with patch.object(auth_mod, "get_conn", lambda: self._conn((True, None))):
            assert auth_mod.verify_jwt(token)["username"] == "bob"


class TestAdminPassword:
    def test_live_mode_random(self):
        with patch("src.data_platform.settings.is_live_trading_enabled", return_value=True):
            pwd = auth_mod._default_admin_password()
        assert pwd != "admin123" and len(pwd) >= 12

    def test_dev_mode_keeps_admin123(self):
        with patch("src.data_platform.settings.is_live_trading_enabled", return_value=False):
            assert auth_mod._default_admin_password() == "admin123"


class TestCardFreshness:
    def test_fresh_card_passes(self):
        import time as _t
        assert card_action_fresh({"ts": _t.time() - 10}) is True

    def test_stale_card_rejected(self):
        import time as _t
        assert card_action_fresh({"ts": _t.time() - 120}) is False

    def test_no_ts_rejected(self):
        """F-33：部署前的旧卡片（无 ts）不可重放。"""
        assert card_action_fresh({"action": "confirm", "tool": "emergency_halt"}) is False

    def test_built_card_carries_ts(self):
        card = build_confirm_card("strategy_stop", {"id": "s1"})
        btn = card["elements"][1]["actions"][0]["value"]
        assert isinstance(btn.get("ts"), (int, float))


class TestVerifyEvidenceGate:
    def test_no_runs_rejected(self):
        from src.data_platform import db as data_db
        from src.web_api.routes.strategy import verify_strategy

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                class Cur:
                    def fetchone(self):
                        return (0,)
                return Cur()

        with patch.object(data_db, "get_conn", lambda: C()):
            with pytest.raises(Exception) as e:
                verify_strategy("some-strategy", body={}, payload={"username": "tester"})
        assert e.value.status_code == 403

    def test_invalid_run_rejected(self):
        from src.data_platform import db as data_db
        from src.web_api.routes.strategy import verify_strategy

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                class Cur:
                    def fetchone(self):
                        return ("failed",)  # 状态非 done
                return Cur()

        with patch.object(data_db, "get_conn", lambda: C()):
            with pytest.raises(Exception) as e:
                verify_strategy("s1", body={"run_id": 5}, payload={"username": "tester"})
        assert e.value.status_code == 400
