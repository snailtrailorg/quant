"""批11D IM 方式注册（两层模型）+自助向导测试（方案 v2 双盲审 A/B 修订后）。

覆盖：①methods 结构+fields 同源②ONBOARDING 派生=any()（顺序无关——A-P1-3/B-P1-2 钉值）
③自助向导端点（幽灵 provider 404 兼路由序钉子/幽灵 method 与合法 manual method 400/
配额 400/频控 429/ticket 归属 404）④重扫三分支（他人 error/平台 owned:false 不翻 enabled/
自己现状）⑤task INSERT 带 owner+audit。
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


ADMIN = {"sub": "1", "username": "admin", "role": "admin", "db_role": "admin"}
USER9 = {"sub": "9", "username": "u9", "role": "viewer", "db_role": "viewer"}


def _client():
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    return TestClient(app)


def _ctx(conn, ident=USER9):
    return [
        patch("src.web_api.auth.verify_jwt", return_value=ident),
        patch("src.web_api.routes.im_bots.get_conn", return_value=conn),
        patch("src.feishu_bot.tasks.get_conn", return_value=conn),
    ]


class TestModel:
    def test_methods_structure_and_fields_same_source(self):
        from src.im_bot.base import list_providers
        ps = list_providers()
        f = next(p for p in ps if p["provider"] == "feishu")
        ids = [m["id"] for m in f["methods"]]
        # 批13（裁定A）：飞书 form 方式砍除——自助向导扫码唯一路径；FIELD_SCHEMA 保留（admin 编辑面）
        assert set(ids) == {"qr"}
        qr = next(m for m in f["methods"] if m["id"] == "qr")
        assert qr["kind"] == "interactive" and qr["wizard"] == "feishu_register"
        # 批13：钉钉/企微=form 单方式，FIELD_SCHEMA 各自声明；manual 方式通用挂 fields（B-P0-1 契约恢复）
        d = next(p for p in ps if p["provider"] == "dingtalk")
        assert [m["id"] for m in d["methods"]] == ["form"] and d["mode"] == "websocket"
        assert {x["key"] for x in d["field_schema"]} == {"app_key", "app_secret"}
        assert d["methods"][0]["fields"] == d["field_schema"]   # A-P2-7 同源本体（B-P0-1 重建断言）
        w = next(p for p in ps if p["provider"] == "wecom")
        assert [m["id"] for m in w["methods"]] == ["form"] and w["mode"] == "websocket"
        assert {x["key"] for x in w["field_schema"]} == {"bot_id", "secret"}
        assert w["methods"][0]["fields"] == w["field_schema"]
        # 批13 UX 裁定：页签顺序=飞书→钉钉→企微（用户指定，非字母序）
        assert [p["provider"] for p in ps if p["provider"] in ("feishu", "dingtalk", "wecom")] == ["feishu", "dingtalk", "wecom"]

    def test_onboarding_derived_any_order_independent(self):
        """A-P1-3/B-P1-2：派生=any(interactive)——form 排前也必须 interactive（首键派生是顺序地雷）。"""
        from src.im_bot.base import IMBotProvider
        class P(IMBotProvider):
            provider = "x"
            MODE = "webhook"
            ONBOARDING_METHODS = {"form": {"kind": "manual"}, "qr": {"kind": "interactive"}}   # form 在前
            def send_text(self, *a, **k): pass
            def send_card(self, *a, **k): pass
            def test_connection(self, *a, **k): return True, ""
        assert P().ONBOARDING == "interactive"
        class Q(IMBotProvider):
            provider = "y"
            ONBOARDING_METHODS = {"form": {"kind": "manual"}}
            def send_text(self, *a, **k): pass
            def send_card(self, *a, **k): pass
            def test_connection(self, *a, **k): return True, ""
        assert Q().ONBOARDING == "manual"


class TestSelfOnboarding:
    def test_ghost_provider_404(self):
        """幽灵 provider 404——兼路由序钉子（若被 /{bid} 吃掉会 422，B-P2-1）。"""
        conn = _conn()
        with contextlib.ExitStack() as s:
            for p in _ctx(conn): s.enter_context(p)
            r = _client().post("/api/my/im-bots/onboarding/ghost/qr", headers={"Authorization": "Bearer t"})
        assert r.status_code == 404

    def test_ghost_method_and_manual_method_400(self):
        for method in ("ghost", "form"):   # 幽灵方式 + 合法 manual 方式（不走向导）
            conn = _conn()
            with contextlib.ExitStack() as s:
                for p in _ctx(conn): s.enter_context(p)
                r = _client().post(f"/api/my/im-bots/onboarding/feishu/{method}",
                                   headers={"Authorization": "Bearer t"})
            assert r.status_code == 400, method

    def test_quota_5(self):
        conn = _conn(scripted=[((5,), [], 0)])   # count=5
        with contextlib.ExitStack() as s:
            for p in _ctx(conn): s.enter_context(p)
            r = _client().post("/api/my/im-bots/onboarding/feishu/qr", headers={"Authorization": "Bearer t"})
        assert r.status_code == 400 and r.json()["code"] == "BOT_QUOTA"

    def test_busy_ticket_429(self):
        conn = _conn(scripted=[((0,), [], 0)])
        rd = MagicMock()
        rd.scan_iter.return_value = iter(["feishu:session:t1"])
        rd.get.return_value = '{"status": "scanning", "owner_user_id": 9}'
        with contextlib.ExitStack() as s:
            for p in _ctx(conn): s.enter_context(p)
            s.enter_context(patch("src.web_api.routes.im_bots.feishu_redis_client", return_value=rd))
            r = _client().post("/api/my/im-bots/onboarding/feishu/qr", headers={"Authorization": "Bearer t"})
        assert r.status_code == 429 and r.json()["code"] == "ONBOARDING_BUSY"

    def test_start_ok_and_task_owner_pinned(self):
        conn = _conn(scripted=[((0,), [], 0)])
        rd = MagicMock(); rd.scan_iter.return_value = iter([]); rd.get.return_value = None
        with contextlib.ExitStack() as s:
            for p in _ctx(conn): s.enter_context(p)
            s.enter_context(patch("src.web_api.routes.im_bots.feishu_redis_client", return_value=rd))
            entry = s.enter_context(patch("src.web_api.routes.im_bots._wizard_entry"))
            s.enter_context(patch("src.web_api.routes.im_bots.audit_log"))
            set_s = s.enter_context(patch("src.feishu_bot.tasks._set_session"))
            s.enter_context(patch("src.feishu_bot.tasks.acquire_onboarding_slot", return_value=True))
            r = _client().post("/api/my/im-bots/onboarding/feishu/qr", headers={"Authorization": "Bearer t"})
        assert r.status_code in (200, 202) and "ticket" in r.json()
        # 盲审 B-P1-2：owner 钉死断言重建（IDOR 防线回归护栏——观察点=端点同步写 pending 的 owner 参,
        # 线程内 entry 调用有竞态不可稳定断言;owner 正确传递由 args=(session_id, uid) 与 pending 同源保证）
        owner_args = [c.kwargs.get("owner_user_id") for c in set_s.call_args_list]
        assert 9 in owner_args

    def test_ticket_owner_binding_404(self):
        """他人 ticket → 404（A-P2-2 session 载荷 owner 比对）。"""
        rd = MagicMock()
        rd.get.return_value = '{"status": "scanning", "owner_user_id": 1}'   # admin 的会话
        with contextlib.ExitStack() as s:
            for p in _ctx(_conn()): s.enter_context(p)
            s.enter_context(patch("src.web_api.routes.im_bots.feishu_redis_client", return_value=rd))
            r = _client().get("/api/my/im-bots/onboarding-status/t9", headers={"Authorization": "Bearer t"})
        assert r.status_code == 404


class TestRescanBranches:
    """重扫三分支（A-P1-1+B-P1-1）——直接测 task 内 _rescan_verdict 语义分支的行为钉子。"""

    def _run_task_rescan(self, row_owner, task_owner, set_session, save_creds):
        """最小驱动：mock lark.register_app 返回固定 app，走 task 主体到落库分支。"""
        import src.feishu_bot.tasks as T
        conn = _conn(scripted=[(None, [], 0),   # SELECT 重查（INSERT 后）——顺序由流程决定，这里直接预置 row 存在路径
                               ])
        # 构造：首次 SELECT 返回既有行（id=10, creds, owner=row_owner）
        conn2 = MagicMock(); conn2.__enter__.return_value = conn2
        cur = MagicMock()
        cur.fetchone.return_value = (10, "enc", row_owner)
        conn2.execute.return_value = cur
        fake_lark = MagicMock()
        fake_lark.register_app.return_value = {"client_id": "cli_x", "client_secret": "s"}
        with patch("src.feishu_bot.tasks.get_conn", return_value=conn2), \
             patch.object(T.lark, "register_app", fake_lark.register_app), \
             patch.object(T, "_set_session", set_session), \
             patch("src.im_bot.credentials.save_bot_credentials", save_creds), \
             patch("src.im_bot.credentials.get_bot_credentials", return_value={}), \
             patch.object(T, "_audit"), \
             patch.object(T, "_current_username", return_value="u9"):
            T.run_onboarding("sess", owner_user_id=task_owner)

    def test_others_bot_error(self):
        states = []
        set_s = lambda sid, d, expire=None, owner_user_id=None: states.append(d)
        self._run_task_rescan(row_owner=1, task_owner=9, set_session=set_s, save_creds=MagicMock())
        final = states[-1]
        assert final["status"] == "error" and "已被其他账号接入" in final["error"]

    def test_platform_bot_creds_only_no_enable_flip(self):
        """平台级 bot（owner NULL）+ 自助重扫 → 只刷凭证：UPDATE enabled 语句不得出现。"""
        states, upd = [], MagicMock()
        set_s = lambda sid, d, expire=None, owner_user_id=None: states.append(d)
        # 用真 conn 捕获 SQL
        sqls = []
        conn = MagicMock(); conn.__enter__.return_value = conn
        cur = MagicMock(); cur.fetchone.return_value = (10, "enc", None)
        conn.execute.side_effect = lambda sql, *a: (sqls.append(sql), cur)[1]
        import src.feishu_bot.tasks as T
        fake_lark = MagicMock()
        fake_lark.register_app.return_value = {"client_id": "cli_x", "client_secret": "s"}
        with patch("src.feishu_bot.tasks.get_conn", return_value=conn), \
             patch.object(T.lark, "register_app", fake_lark.register_app), \
             patch.object(T, "_set_session", lambda sid, d, expire=600, owner_user_id=None: set_s(sid, d, expire)), \
             patch("src.im_bot.credentials.save_bot_credentials", upd), \
             patch("src.im_bot.credentials.get_bot_credentials", return_value={}), \
             patch.object(T, "_audit"), \
             patch.object(T, "_current_username", return_value="u9"):
            T.run_onboarding("sess", owner_user_id=9)
        assert states[-1]["status"] == "done" and states[-1]["owned"] is False
        assert upd.called                                   # 凭证刷了
        assert not any("enabled=true" in s for s in sqls)     # 不翻 enabled（A-P1-1）

    def test_own_rescan_keeps_legacy(self):
        """自己/admin 重扫 → 现状：翻 enabled+改名。"""
        states = []
        set_s = lambda sid, d, expire=None, owner_user_id=None: states.append(d)
        sqls = []
        conn = MagicMock(); conn.__enter__.return_value = conn
        cur = MagicMock(); cur.fetchone.return_value = (10, "enc", 9)
        conn.execute.side_effect = lambda sql, *a: (sqls.append(sql), cur)[1]
        import src.feishu_bot.tasks as T
        fake_lark = MagicMock()
        fake_lark.register_app.return_value = {"client_id": "cli_x", "client_secret": "s"}
        with patch("src.feishu_bot.tasks.get_conn", return_value=conn), \
             patch.object(T.lark, "register_app", fake_lark.register_app), \
             patch.object(T, "_set_session", lambda sid, d, expire=600, owner_user_id=None: set_s(sid, d, expire)), \
             patch("src.im_bot.credentials.save_bot_credentials", MagicMock()), \
             patch("src.im_bot.credentials.get_bot_credentials", return_value={}), \
             patch.object(T, "_audit"), \
             patch.object(T, "_current_username", return_value="u9"):
            T.run_onboarding("sess", owner_user_id=9)
        assert states[-1]["status"] == "done" and states[-1]["owned"] is True
        assert any("enabled=true" in s for s in sqls)


class TestPostCodeReview:
    """代码双盲审修复的补强钉子。"""

    def test_audit_uses_helper_columns(self):
        """P1-2：审计走 data_platform.audit_log 正源（原裸 INSERT 列名 username 不存在=静默全灭）。"""
        import pathlib
        src = pathlib.Path("src/feishu_bot/tasks.py").read_text()
        assert "INSERT INTO audit_log" not in src
        assert "from src.data_platform.audit import audit_log" in src

    def test_session_payload_always_carries_owner(self):
        """P1-3：_set_session 自动注入 owner——scanning/done 覆盖不再丢归属（频控/比对载体）。"""
        import src.feishu_bot.tasks as T
        import json as _j
        rd = MagicMock()
        T._redis = rd
        T._set_session("s1", {"status": "scanning", "qr_img": "x"}, expire=600, owner_user_id=9)   # 批12A：owner 显式传参（_SESSION_OWNER 退役）
        payload = _j.loads(rd.setex.call_args[0][2])
        assert payload["owner_user_id"] == 9 and payload["status"] == "scanning" and "ts" in payload

    def test_own_status_200(self):
        """B-P2-1 附：本人 ticket 轮询 200（归属比对只拒他人）。"""
        rd = MagicMock()
        rd.get.return_value = '{"status": "scanning", "owner_user_id": 9}'
        with contextlib.ExitStack() as s:
            for p in _ctx(_conn(), USER9): s.enter_context(p)
            s.enter_context(patch("src.web_api.routes.im_bots.feishu_redis_client", return_value=rd))
            r = _client().get("/api/my/im-bots/onboarding-status/t9", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200 and r.json()["status"] == "scanning"

    def test_insert_conflict_rescan_path(self):
        """A-P2-6：INSERT 被并发冲突吞（RETURNING None）→ 重查行走三分支。"""
        import src.feishu_bot.tasks as T
        states, sqls = [], []
        conn = MagicMock(); conn.__enter__.return_value = conn
        cur1, cur2 = MagicMock(), MagicMock()
        cur1.fetchone.return_value = None                      # 首查无行
        cur2.fetchone.return_value = (10, "enc", 1)            # 重查=他人行（并发已插）
        cur3 = MagicMock(); cur3.fetchone.return_value = None  # INSERT RETURNING=None（被吞）
        conn.execute.side_effect = lambda sql, *a: (sqls.append(sql), cur3 if "RETURNING" in sql else (cur2 if "SELECT" in sql and sqls.count(sql) >= 1 and "route_key" in sql and "id, credentials" in sql else cur1))[1]
        # 简化：按调用序——首查/INSERT/重查
        calls = {"n": 0}
        def exe(sql, *a):
            calls["n"] += 1; sqls.append(sql)
            cc = MagicMock()
            cc.fetchone.return_value = [None, None, (10, "enc", 1)][min(calls["n"] - 1, 2)] if calls["n"] <= 3 else None
            if calls["n"] == 2: cc.fetchone.return_value = None   # INSERT RETURNING 被吞
            return cc
        conn.execute.side_effect = exe
        fake = MagicMock(); fake.register_app.return_value = {"client_id": "cli_c", "client_secret": "s"}
        with patch("src.feishu_bot.tasks.get_conn", return_value=conn), \
             patch.object(T.lark, "register_app", fake.register_app), \
             patch.object(T, "_set_session", lambda sid, d, expire=600, owner_user_id=None: states.append(d)), \
             patch("src.im_bot.credentials.save_bot_credentials", MagicMock()), \
             patch("src.im_bot.credentials.get_bot_credentials", return_value={}), \
             patch.object(T, "_audit"), \
             patch.object(T, "_current_username", return_value="u9"), \
             patch("httpx.post", return_value=MagicMock(json=lambda: {})), \
             patch("httpx.get", return_value=MagicMock(json=lambda: {})):
            T.run_onboarding("s", owner_user_id=9)
        assert states[-1]["status"] == "error" and states[-1]["code"] == "ONBOARDING_APP_TAKEN"
