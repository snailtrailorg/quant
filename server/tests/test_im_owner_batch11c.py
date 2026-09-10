"""批11C IM 归属用户（pool 化）测试（方案 v2 双盲审 A/B 修订后）。

覆盖：①resolve_im_identity（绑定优先/多义拒/停用拒/未绑定 None）②自助面
（owner 钉死+default_role 恒 viewer+IDOR 404+providers 可达）③绑定 user_id
会话钉死④平台级卡片门（无平台 bot 拒/perm 键判定）⑤pool 对账（起/停/退避）。
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


def _ctx(conn, ident=ADMIN):
    return [
        patch("src.web_api.auth.verify_jwt", return_value=ident),
        patch("src.web_api.routes.im_bots.get_conn", return_value=conn),
        patch("src.data_platform.db.get_conn", return_value=conn),
    ]


class TestResolveIdentity:
    def test_bound_ok(self):
        from src.im_bot.users import resolve_im_identity
        conn = _conn(scripted=[(None, [(9,)], 0), (("u9", "viewer"), [], 0)])
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch("src.data_platform.perms.load_effective_permissions", return_value=({"read"}, {})):
            r = resolve_im_identity("ou_x")
        assert r["user_id"] == 9 and r["username"] == "u9" and r["perms"] == {"read"}

    def test_unbound_none_and_first_seen_note_path(self):
        """未绑定=None（fail-closed）——首见留痕行 user_id NULL 不进绑定查询结果。"""
        from src.im_bot.users import resolve_im_identity
        conn = _conn(scripted=[(None, [], 0)])
        with patch("src.data_platform.db.get_conn", return_value=conn):
            assert resolve_im_identity("ou_x") is None

    def test_ambiguous_multi_bind_rejected(self):
        from src.im_bot.users import resolve_im_identity
        conn = _conn(scripted=[(None, [(9,), (1,)], 0)])   # 跨 bot 绑到不同账号
        with patch("src.data_platform.db.get_conn", return_value=conn):
            assert resolve_im_identity("ou_x") is None

    def test_disabled_account_rejected(self):
        """绑定账号停用/软删 → None（A-P1-2）。"""
        from src.im_bot.users import resolve_im_identity
        conn = _conn(scripted=[(None, [(9,)], 0), (None, [], 0)])   # users 行查无（enabled/deleted 过滤后）
        with patch("src.data_platform.db.get_conn", return_value=conn):
            assert resolve_im_identity("ou_x") is None


class TestSelfService:
    def test_create_pins_owner_and_role(self):
        """自助创建：owner=会话 sub、default_role 服务端恒 viewer（body 传 admin 也被忽略，A-P0-1）。"""
        # 批13（A-P1-5）：create 新增配额 count（→0）→ 唯一性预检空 → INSERT RETURNING 42
        conn = _conn(scripted=[((0,), [], 0), (None, [], 0), ((42,), [], 1)])
        with contextlib.ExitStack() as s:
            for p in _ctx(conn, USER9): s.enter_context(p)
            s.enter_context(patch("src.web_api.routes.im_bots.audit_log"))
            r = _client().post("/api/my/im-bots", headers={"Authorization": "Bearer t"},
                               json={"provider": "feishu", "name": "my bot", "description": "",
                                     "default_role": "admin", "owner_user_id": 1,   # 应被忽略
                                     "credentials": {"app_id": "a1", "app_secret": "s1"}})
        assert r.status_code == 200 and r.json() == {"id": 42}
        ins = [c[0] for c in conn.execute.call_args_list if "INSERT INTO im_bot_config" in c[0][0]]
        assert ins, "应有 INSERT"
        sql_text, args = ins[0][0], ins[0][1]
        assert "'viewer'" in sql_text       # default_role 服务端内嵌恒 viewer（非参数化）
        assert args[3] == 9                 # owner=会话 sub（body 的 owner_user_id=1 被忽略）

    def test_idor_404(self):
        """IDOR：动他人/平台级 bot → 404（防探测，A-P2-2）。"""
        conn = _conn(scripted=[(None, [], 0)])
        with contextlib.ExitStack() as s:
            for p in _ctx(conn, USER9): s.enter_context(p)
            r = _client().delete("/api/my/im-bots/10", headers={"Authorization": "Bearer t"})
        assert r.status_code == 404 and r.json()["code"] == "BOT_NOT_FOUND"


class TestCardGate:
    def test_no_platform_bot_rejected(self):
        """无平台级 bot（owner NULL）→ 卡片确认拒（自助 bot 请走 Web，A-P1-1）。"""
        # 卡片面第一步=身份解析（router ②闸）——未绑定即拒；平台 bot 查询面由 router 内联（集成面 staging 验）
        from src.im_bot.users import resolve_im_identity
        conn2 = _conn(scripted=[(None, [], 0)])
        with patch("src.data_platform.db.get_conn", return_value=conn2):
            assert resolve_im_identity("ou_x") is None


class TestPoolAccounting:
    def test_desired_fail_safe(self):
        """对账查询失败返回 None（≠{}——None=跳过周期不动现状；{}=terminate 全部，代码盲审 P0 修）。"""
        from src.im_bot import pool
        bad = MagicMock(); bad.__enter__.side_effect = RuntimeError("db down")
        with patch("src.data_platform.db.get_conn", return_value=bad):
            assert pool._desired() is None

    def test_child_backoff(self):
        """子进程退出 → 退避递增封顶（B-P0-2 坏 bot 不打满重试）。批13：_Child 增 provider 形参。"""
        import time as _t
        from src.im_bot import pool
        ch = pool._Child(7, "feishu")
        ch.proc = MagicMock()
        ch.proc.poll.return_value = 1
        ch.reap()
        assert ch.next_start >= _t.monotonic() and ch.backoff == pool._BACKOFF_BASE * 2
        for _ in range(6):
            ch.proc = MagicMock(); ch.proc.poll.return_value = 1
            ch.reap()
        assert ch.backoff <= pool._BACKOFF_MAX


class TestPostReview:
    """代码双盲审修复的钉子测试。"""

    def test_gateway_read_gate(self):
        """A-P1-3：perms 无 read 键=零工具（Web 面同被拒的用户 IM 面不给读工具）。"""
        from src.llm_gateway.gateway import LLMGateway
        gw = LLMGateway.__new__(LLMGateway)   # 绕 __init__（不连 DB）
        assert gw._filter_tools("viewer", None, perms=set()) == []
        assert gw._filter_tools("viewer", None, perms={"read"}) != []

    def test_seed_keeps_platform_bots(self):
        """A-P0-1/B-P0-2 修：0072 不再把存量 bot 归 admin（保留 NULL=平台级）——迁移源码钉子。"""
        import pathlib
        src = pathlib.Path("migrations/versions/0072_im_owner.py").read_text()
        assert "UPDATE im_bot_config SET owner_user_id" not in src
        assert "保留 owner NULL=平台级" in src

    def test_dispatch_recipients_bound_only(self):
        """A-P1-4/B-P1-2：dispatch 收件人只取绑定行（user_id 非空）。"""
        import pathlib
        src = pathlib.Path("src/alert_notify/dispatch.py").read_text()
        assert "user_id IS NOT NULL" in src
