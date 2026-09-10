"""批12A 扫码出码秒回测试（方案 v2 双盲审 A/B 修订后）。

覆盖：①出码同步返回（Event 语义：qr 直返/快失败短路 502/超时 202 回落）
②Semaphore 全局帽（满 429/正常 acquire-release）③cancel_event 传递+看门狗 ④confirming
按真实语义触发（register_app 返回后——**禁 mock on_status_change 伪造**，A/B 同判 P0）
⑤startup 死会话清扫 ⑥429 extra existing_ticket 通道 ⑦key 不存在→expired ⑧应用名后缀。
"""
from unittest.mock import patch, MagicMock

import contextlib


def _conn(scripted=None):
    conn = MagicMock(); conn.__enter__.return_value = conn
    calls = {"n": 0}
    def execute(sql, *a):
        i = calls["n"]; calls["n"] += 1
        cur = MagicMock()
        if scripted and i < len(scripted):
            cur.fetchone.return_value, cur.fetchall.return_value, cur.rowcount = scripted[i]
        else:
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []
            cur.rowcount = 0
        return cur
    conn.execute.side_effect = execute
    return conn


USER9 = {"sub": "9", "username": "u9", "role": "viewer", "db_role": "viewer"}


def _client():
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    return TestClient(app)


def _ctx(conn, ident=USER9):
    return [
        patch("src.web_api.auth.verify_jwt", return_value=ident),
        patch("src.web_api.routes.im_bots.get_conn", return_value=conn),
        patch("src.data_platform.db.get_conn", return_value=conn),
    ]


class TestSyncEndpoint:
    def _post(self, conn, rd, entry_fn=None, mock_slot=True):
        with contextlib.ExitStack() as s:
            for p in _ctx(conn): s.enter_context(p)
            s.enter_context(patch("src.web_api.routes.im_bots.feishu_redis_client", return_value=rd))
            s.enter_context(patch("src.web_api.routes.im_bots._wizard_entry",
                                  return_value=entry_fn or (lambda *a, **k: None)))
            s.enter_context(patch("src.web_api.routes.im_bots.audit_log"))
            s.enter_context(patch("src.feishu_bot.tasks._set_session"))
            if mock_slot:
                s.enter_context(patch("src.feishu_bot.tasks.acquire_onboarding_slot", return_value=True))
            return _client().post("/api/my/im-bots/onboarding/feishu/qr",
                                  headers={"Authorization": "Bearer t"})

    def test_qr_sync_return(self):
        """正常路径：on_qr 在 5s 内触发 → 200 直接带 qr_img（零轮询）。"""
        conn = _conn(scripted=[((0,), [], 0)])
        rd = MagicMock(); rd.scan_iter.return_value = iter([]); rd.get.return_value = None
        def entry(sid, owner, on_qr=None, on_error=None, **k):
            on_qr({"qr_url": "https://x", "qr_img": "data:...", "expire_in": 600})
        r = self._post(conn, rd, entry_fn=entry)
        assert r.status_code == 200 and r.json()["qr_img"] == "data:..."

    def test_fast_fail_short_circuit(self):
        """快失败：on_error 触发 → 502 不等满 5s（A-P2-4）。"""
        conn = _conn(scripted=[((0,), [], 0)])
        rd = MagicMock(); rd.scan_iter.return_value = iter([]); rd.get.return_value = None
        def entry(sid, owner, on_qr=None, on_error=None, **k):
            on_error("init boom")
        r = self._post(conn, rd, entry_fn=entry)
        assert r.status_code == 502 and r.json()["code"] == "ONBOARDING_FAILED"

    def test_busy_returns_existing_ticket(self):
        """429 带 existing_ticket（B-P2-3 extra 通道；扫描按 owner 过滤=必自己的）。"""
        conn = _conn(scripted=[((0,), [], 0)])
        rd = MagicMock()
        rd.scan_iter.return_value = iter(["feishu:session:t-live"])
        rd.get.return_value = '{"status": "scanning", "owner_user_id": 9}'
        r = self._post(conn, rd)
        assert r.status_code == 429
        assert r.json().get("existing_ticket") == "t-live"

    def test_semaphore_full_429(self):
        """全局帽满 → 429（A-P1-1②：SDK 无 timeout 线程总闸）。"""
        import src.feishu_bot.tasks as T
        conn = _conn(scripted=[((0,), [], 0)])
        rd = MagicMock(); rd.scan_iter.return_value = iter([]); rd.get.return_value = None
        for _ in range(8):               # 占满全部 8 位（mock_slot=False——测真信号量）
            T._ONBOARD_SEMAPHORE.acquire()
        try:
            r = self._post(conn, rd, mock_slot=False)
            assert r.status_code == 429
        finally:
            for _ in range(8):
                T._ONBOARD_SEMAPHORE.release()

    def test_status_missing_key_expired(self):
        """A-P2-6：key 不存在 → expired（原 pending 混同收口）。"""
        rd = MagicMock(); rd.get.return_value = None
        with contextlib.ExitStack() as s:
            for p in _ctx(_conn()): s.enter_context(p)
            s.enter_context(patch("src.web_api.routes.im_bots.feishu_redis_client", return_value=rd))
            r = _client().get("/api/my/im-bots/onboarding-status/ghost", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200 and r.json()["status"] == "expired"


class TestOnboardingCore:
    def test_cancel_event_passed(self):
        """cancel_event 传给 register_app（A-P1-1：SDK poll 间检查的退出通道）。"""
        import src.feishu_bot.tasks as T
        captured = {}
        conn = _conn()
        def fake_register(**kw):
            captured.update(kw)
            return {"client_id": "", "client_secret": ""}
        with patch("src.feishu_bot.tasks.get_conn", return_value=conn), \
             patch.object(T.lark, "register_app", side_effect=fake_register), \
             patch.object(T, "_set_session"), \
             patch.object(T, "_current_username", return_value="u9"), \
             patch.object(T, "_audit"), \
             patch("httpx.post", return_value=MagicMock(json=lambda: {})), \
             patch("httpx.get", return_value=MagicMock(json=lambda: {})):
            T.run_onboarding("s1", owner_user_id=9)
        assert "cancel_event" in captured and captured["cancel_event"] is not None

    def test_confirming_written_after_register_returns(self):
        """P0 修（A/B 同判）：confirming 在 register_app 返回后写入——按真实语义（非 on_status_change）。"""
        import src.feishu_bot.tasks as T
        events = []
        conn = _conn()
        def fake_register(**kw):
            events.append("register_return")   # SDK 返回瞬间打标
            return {"client_id": "cli_x", "client_secret": "s"}
        def cap_set(sid, d, expire=600, owner_user_id=None):
            events.append(d.get("status"))
        with patch("src.feishu_bot.tasks.get_conn", return_value=conn), \
             patch.object(T.lark, "register_app", side_effect=fake_register), \
             patch.object(T, "_set_session", side_effect=cap_set), \
             patch.object(T, "_current_username", return_value="u9"), \
             patch.object(T, "_audit"), \
             patch("src.im_bot.credentials.save_bot_credentials", MagicMock()), \
             patch("httpx.post", return_value=MagicMock(json=lambda: {})), \
             patch("httpx.get", return_value=MagicMock(json=lambda: {})):
            T.run_onboarding("s2", owner_user_id=9)
        # confirming 必须紧跟 register_return 之后（before done/error）
        i = events.index("register_return")
        assert events[i + 1] == "confirming"

    def test_semaphore_released_on_success(self):
        """成功路径 finally 归还全局帽。"""
        import src.feishu_bot.tasks as T
        conn = _conn()
        with patch("src.feishu_bot.tasks.get_conn", return_value=conn), \
             patch.object(T.lark, "register_app", return_value={"client_id": "", "client_secret": ""}), \
             patch.object(T, "_set_session"), \
             patch.object(T, "_current_username", return_value="u9"), \
             patch.object(T, "_audit"), \
             patch("httpx.post", return_value=MagicMock(json=lambda: {})), \
             patch("httpx.get", return_value=MagicMock(json=lambda: {})):
            T.run_onboarding("s3", owner_user_id=9)
        # BoundedSemaphore 可再 acquire 8 次（说明归还）
        for _ in range(8):
            assert T._ONBOARD_SEMAPHORE.acquire(blocking=False)
        for _ in range(8):
            T._ONBOARD_SEMAPHORE.release()

    def test_app_suffix(self):
        import src.feishu_bot.tasks as T
        assert T._app_suffix("bernard") == "bernard"
        assert T._app_suffix("张三!@#") == "张三"
        assert T._app_suffix("") == "用户"
        assert T._app_suffix(None) == "用户"
        assert T._app_suffix(10) == "用户"   # 非 str 容忍

    def test_sweep_stale_sessions(self):
        """startup 清扫：非终态→error/ONBOARDING_INTERRUPTED；终态不动。"""
        import src.feishu_bot.tasks as T
        import json as _j
        rd = MagicMock()
        rd.scan_iter.return_value = iter(["feishu:session:live1", "feishu:session:done1"])
        rd.get.side_effect = lambda k: _j.dumps(
            {"status": "scanning", "owner_user_id": 9} if "live1" in k else {"status": "done", "owner_user_id": 9})
        rd.ttl.return_value = 300
        with patch.object(T, "_redis", rd):
            n = T.sweep_stale_sessions()
        assert n == 1
        written = _j.loads(rd.setex.call_args[0][2])
        assert written["code"] == "ONBOARDING_INTERRUPTED" and written["status"] == "error"
