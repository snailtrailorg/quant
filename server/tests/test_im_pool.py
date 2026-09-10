"""批13 · pool 通用化单测（首次给 pool 补测试）。

覆盖：_desired 多平台形状（provider 维度）/spawn 入口映射（feishu 存量不动+新平台 runners）/
未注册 provider skip/凭证指纹重启/DB 失败 None fail-safe（语义随迁钉子）。
"""
import time
from unittest.mock import MagicMock, patch


class TestDesired:
    def test_multi_provider_shape(self):
        """批13：对账目标含全平台 {bid: (provider, fingerprint)}。"""
        from src.im_bot import pool
        conn = MagicMock(); conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = [
            (1, "feishu", "enc1"), (2, "dingtalk", "enc2"), (3, "wecom", "enc3"), (4, "feishu", "")]
        with patch("src.data_platform.db.get_conn", return_value=conn):
            assert pool._desired() == {1: ("feishu", "enc1"), 2: ("dingtalk", "enc2"),
                                       3: ("wecom", "enc3"), 4: ("feishu", "")}

    def test_db_fail_returns_none(self):
        """fail-safe 语义钉子（B-P0-1）：DB 失败=None≠{}——None=跳周期，{}=全 terminate。"""
        from src.im_bot import pool
        bad = MagicMock(); bad.__enter__.side_effect = RuntimeError("db down")
        with patch("src.data_platform.db.get_conn", return_value=bad):
            assert pool._desired() is None


class TestSpawnMapping:
    def test_runner_modules_cover_all_registered_providers(self):
        """入口映射完备性：注册表里每个 provider 都有 runner（缺=静默死 bot，A-P1-4）。"""
        from src.im_bot import pool
        from src.im_bot.base import list_providers
        registered = {p["provider"] for p in list_providers()}
        assert registered <= set(pool._RUNNER_MODULES)

    def test_feishu_entry_unchanged(self):
        """存量入口不动（现网飞书子进程零迁移）。"""
        from src.im_bot import pool
        assert pool._RUNNER_MODULES["feishu"] == ["src.feishu_bot.ws_client"]
        assert pool._RUNNER_MODULES["dingtalk"] == ["src.im_bot.runners.dingtalk"]
        assert pool._RUNNER_MODULES["wecom"] == ["src.im_bot.runners.wecom"]

    def test_unknown_provider_skipped_no_spawn(self):
        """未注册 provider（映射缺）=log skip 不 spawn（前向兼容兜底）。"""
        from src.im_bot import pool
        ch = pool._Child(9, "telegram")
        with patch.object(pool.subprocess, "Popen") as mp:
            ch.ensure("fp")
        mp.assert_not_called()

    def test_fingerprint_change_restarts(self):
        """凭证指纹变化=terminate 重起（换 token 语义跨平台保持）。"""
        from src.im_bot import pool
        ch = pool._Child(7, "dingtalk")
        old_proc = MagicMock(); old_proc.poll.return_value = None
        ch.proc = old_proc                              # 存活
        ch.fingerprint = "old"
        with patch.object(pool.subprocess, "Popen") as mp:
            ch.ensure("new")
        old_proc.terminate.assert_called_once()          # 旧进程被停（断言钉旧引用——ensure 会换新 mock）
        assert mp.called                                # 新进程已拉
        assert mp.call_args[0][0][1:] == ["-m", "src.im_bot.runners.dingtalk", "7"]

    def test_backoff_window_blocks_respawn(self):
        from src.im_bot import pool
        ch = pool._Child(7, "dingtalk")
        ch.next_start = time.monotonic() + 999   # 退避窗内
        with patch.object(pool.subprocess, "Popen") as mp:
            ch.ensure("fp")
        mp.assert_not_called()


class TestCredentialsRouteKey:
    """验收 #2（盲审 A 测试缺口）：凭证补录不丢 route_key——钉钉 app_key 进名单后
    partial 合并仍保非空 route（P0 修的另一半：补录曾把存量 route 改写为空再撞唯一索引）。"""

    def test_partial_merge_keeps_route(self):
        import json as _json
        from unittest.mock import call
        from src.im_bot import credentials as C
        # 现值 app_key=ak9/app_secret=s1；补录只改 app_secret
        with patch.object(C, "get_bot_credentials",
                          return_value={"app_key": "ak9", "app_secret": "s1"}), \
             patch("src.data_platform.db.get_conn") as mg:
            conn = mg.return_value.__enter__.return_value
            C.save_bot_credentials(7, {"app_secret": "s2"}, partial=True)
        sql_params = conn.execute.call_args[0][1]   # (enc, json_params, bot_id)
        assert _json.loads(sql_params[1])["route_key"] == "ak9"   # 非空且正确
