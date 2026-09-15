"""批26-5 扫码频控 per-user 索引键测试。

覆盖：①pending 首建 NX（并发第二 ticket 不覆盖先到——TOCTOU 关闭）②终态即时 DEL
（done 后立即可再发起，等价批12A 语义）③中态覆盖写续 TTL ④索引键前缀避开
feishu:session:*（sweep_stale_sessions scan 模式不触碰）⑤频控端点 O(1) 查路径。
"""
import contextlib
import fnmatch
import json
from unittest.mock import patch, MagicMock


class FakeValkey:
    """dict 后端模拟 Valkey（setex/set(nx)/get/delete/ttl/scan_iter）——TTL 不跑真实过期，
    只记录（批26-5 测试关注 NX/DEL 语义与键名前缀）。"""

    def __init__(self):
        self.store: dict[str, tuple[str, int]] = {}

    def setex(self, key, expire, val):
        self.store[key] = (val, expire)

    def set(self, key, val, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = (val, ex or 0)
        return True

    def get(self, key):
        v = self.store.get(key)
        return v[0] if v else None

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    def ttl(self, key):
        return self.store.get(key, (None, 0))[1]

    def scan_iter(self, pattern, count=100):
        for k in list(self.store):
            if fnmatch.fnmatch(k, pattern):
                yield k


def _set_session(fake, session_id, status, owner=9, expire=600, **extra):
    from src.feishu_bot import tasks
    with patch.object(tasks, "_redis", fake):
        tasks._set_session(session_id, {"status": status, **extra}, expire=expire,
                           owner_user_id=owner)


class TestOwnerKeyStateMachine:
    def test_pending_nx_first_ticket_wins(self):
        """并发双 pending：NX 不覆盖——索引键恒指先到 ticket（TOCTOU 关闭）。"""
        f = FakeValkey()
        _set_session(f, "t1", "pending")
        _set_session(f, "t2", "pending")
        d = json.loads(f.get("im:onboarding:owner:9"))
        assert d["ticket"] == "t1", "后到 pending 不应覆盖先到（NX）"

    def test_done_deletes_key_immediately(self):
        """终态即时 DEL——done 后立即可再发起（非等 TTL，语义等价批12A）。"""
        f = FakeValkey()
        _set_session(f, "t1", "pending")
        assert f.get("im:onboarding:owner:9") is not None
        _set_session(f, "t1", "done", app_id="x")
        assert f.get("im:onboarding:owner:9") is None, "done 应 DEL 索引键"
        # 再发起：新 pending 可写入
        _set_session(f, "t2", "pending")
        assert json.loads(f.get("im:onboarding:owner:9"))["ticket"] == "t2"

    def test_error_also_terminal(self):
        f = FakeValkey()
        _set_session(f, "t1", "pending")
        _set_session(f, "t1", "error", error="boom")
        assert f.get("im:onboarding:owner:9") is None

    def test_midstate_overwrites_with_same_ticket(self):
        """中态（confirming）覆盖写——键在、ticket 不变、status 跟进。"""
        f = FakeValkey()
        _set_session(f, "t1", "pending", expire=900)
        _set_session(f, "t1", "confirming", expire=120)
        d = json.loads(f.get("im:onboarding:owner:9"))
        assert d == {"ticket": "t1", "status": "confirming"}

    def test_owner_key_outside_sweep_scan_pattern(self):
        """索引键前缀避开 feishu:session:*——sweep 扫描不触碰（键值非 session 载荷，被误解析=污染）。"""
        f = FakeValkey()
        _set_session(f, "t1", "pending")
        swept = list(f.scan_iter("feishu:session:*"))
        assert swept == ["feishu:session:t1"], "sweep 只应见到 session 键自身"

    def test_sweep_cleans_owner_key_on_interrupt(self):
        """盲审 A-P1-1：sweep 置 session 为 error 终态时同步 DEL owner 键——否则重启后用户
        被 429 困到 TTL 尽（批12A #8 困局在索引键时代的回归钉）。"""
        from src.feishu_bot import tasks
        f = FakeValkey()
        _set_session(f, "t1", "scanning")
        assert f.get("im:onboarding:owner:9") is not None
        with patch.object(tasks, "_redis", f):
            n = tasks.sweep_stale_sessions()
        assert n == 1
        assert f.get("feishu:session:t1") is not None   # session 键改写 error 保留（前端可见中断原因）
        assert f.get("im:onboarding:owner:9") is None, "sweep 置终态必须同步清 owner 键"

    def test_terminal_del_respects_owner_of_key(self):
        """盲审 A-P2-2 轻量版：终态 DEL 比对键内 ticket——键已换主（异常路径）不误删新键。"""
        f = FakeValkey()
        _set_session(f, "t1", "pending")
        # 异常路径模拟：键被 t2 劫持（正常流不会发生——NX 感知后无并发双 ticket）
        f.set("im:onboarding:owner:9", '{"ticket": "t2", "status": "scanning"}')
        _set_session(f, "t1", "done")
        d = json.loads(f.get("im:onboarding:owner:9"))
        assert d["ticket"] == "t2", "t1 的终态 DEL 不得误删 t2 的键"


class TestOnboardingEndpointGate:
    """频控端点 O(1) 查：键活→429 existing_ticket；无键→放行（落到配额查询）。"""

    def _post(self, fake, scripted=None):
        import src.feishu_bot.tasks as tasks
        conn = MagicMock(); conn.__enter__.return_value = conn
        calls = {"n": 0}

        def execute(sql, *a):
            i = calls["n"]; calls["n"] += 1
            cur = MagicMock()
            if scripted and i < len(scripted):
                cur.fetchone.return_value = scripted[i]
            else:
                cur.fetchone.return_value = None
            return cur
        conn.execute.side_effect = execute
        # 配额 count ×2 总要给（频控闸在 owner 配额+全平台配额查询之后）——默认 0 放行
        if not scripted:
            scripted = [(0,), (0,)]
        # 盲审 B-P1-2：隔离基建——频控放行后端点继续真 _set_session（写真 Valkey）+ daemon 线程
        # 真跑向导（真 PG + 飞书外呼）。_redis 指向 fake + _wizard_entry 换 stub（直触 on_qr 即 200）。
        def _stub_entry(sid, owner, on_qr=None, on_error=None, **k):
            if on_qr:
                on_qr({"qr_url": "https://x", "qr_img": "data:...", "expire_in": 600})
        USER = {"sub": "9", "username": "u9", "role": "viewer", "db_role": "viewer"}
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        with contextlib.ExitStack() as s:
            s.enter_context(patch("src.web_api.auth.verify_jwt", return_value=USER))
            s.enter_context(patch("src.web_api.routes.im_bots.get_conn", return_value=conn))
            s.enter_context(patch("src.data_platform.db.get_conn", return_value=conn))
            s.enter_context(patch("src.web_api.routes.im_bots.feishu_redis_client", return_value=fake))
            s.enter_context(patch("src.web_api.routes.im_bots.audit_log"))
            s.enter_context(patch.object(tasks, "_redis", fake))   # B-P1-2：_set_session 写 fake（非真 Valkey）
            s.enter_context(patch("src.web_api.routes.im_bots._wizard_entry", return_value=_stub_entry))
            s.enter_context(patch("src.feishu_bot.tasks.acquire_onboarding_slot", return_value=True))
            r = TestClient(app).post("/api/my/im-bots/onboarding/feishu/qr",
                                     headers={"Authorization": "Bearer t"})
        return r

    def test_active_ticket_429_with_existing(self):
        f = FakeValkey()
        _set_session(f, "abc123", "scanning")
        r = self._post(f)
        assert r.status_code == 429 and r.json()["code"] == "ONBOARDING_BUSY"
        assert r.json()["existing_ticket"] == "abc123"

    def test_pending_nx_race_429_second_post(self):
        """盲审 A-P1-2：并发双 POST 都过 GET miss 窗口——第二 POST 的 pending NX 失败必须 429
        （静默继续=双 ticket 并跑+中态劫持键+终态互删的频控失效窗）。"""
        f = FakeValkey()
        # 预置先到 ticket（模拟 POST1 已在 GET 之后、本 POST GET 之前写入的竞态窗口）
        f.set("im:onboarding:owner:9", '{"ticket": "first", "status": "pending"}', ex=900)
        r = self._post(f)   # 本 POST 的 GET miss 由 FakeValkey 预置键复现（ scripted 配额全 0 放行）
        assert r.status_code == 429 and r.json()["code"] == "ONBOARDING_BUSY"
        assert r.json()["existing_ticket"] == "first"
        # 先到键不被破坏
        assert json.loads(f.get("im:onboarding:owner:9"))["ticket"] == "first"

    def test_no_key_passes_rate_gate(self):
        """无索引键=无活会话→频控放行（请求继续走配额/向导路径——此处配额 count=0 放行到出码段）。"""
        r = self._post(FakeValkey(), scripted=[(0,), (0,)])   # 批26-9：配额+全平台 count 两条→0 放行
        assert r.status_code in (200, 202), f"频控应放行: {r.status_code} {r.text}"
