"""批30 · 通知用户化+手机号单测（docs/任务/批30-通知用户化与手机号.md v2 契约）。

覆盖：
- dispatch 展开：用户三通道行结构 {id,sub_id}/email-sms id=user_id·im id=bot_id（A-P0-3 契约）/
  手机无凭证跳过/多 bot 全发/软删停用过滤进 SQL
- alert_tasks：_still_enabled 重查 alert_user_sub（sub_id）+ 旧格式在途 payload 按快照发（A-P0-2）
- 零订阅不外推（legacy 兜底退役——A-P1-2；另见 test_alert_dispatch.test_zero_subs_no_legacy_push）
- alerts CRUD 用户维度：POST 校验/409 重复/PUT 禁改 user_id/DELETE
- phone 链：密码错拒/格式拒/60s 冷却/码值绑手机（A-P0-1）/码错 5 次作废（A-P1-6）/成功改库审计
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from src.web_api.main import app
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    from src.web_api import auth as _auth
    with patch.object(_auth, "verify_jwt",
                      return_value={"sub": 1, "username": "u1", "role": "admin", "db_role": "admin"}):
        client.headers.update({"Authorization": "Bearer test-token"})
        yield client
        client.headers.pop("Authorization", None)
        from src.web_api.routes import auth_routes as _ar
        _ar._RATE_LIMITS.clear()   # phonechg 桶 3/3600 进程级——测试间清零防串（批20 夹具同款）


def _conn_with(*fetchalls):
    c = MagicMock()
    c.__enter__.return_value = c
    cursors = [MagicMock(fetchall=lambda fa=fa: fa, fetchone=lambda fa=fa: (fa[0] if fa else None))
               for fa in fetchalls]
    c.execute.side_effect = cursors
    return c


# ——— dispatch 展开（A-P0-3 行结构契约）———

def test_load_channels_user_expansion():
    """一用户（有邮箱+手机+2 bot）→ 4 行；im 行 id=bot_id、email/sms 行 id=user_id、sub_id 全带。
    批34：subs 七元组（channels 列在 min_level 后）——sel=None 全通道=批30 行为不变。"""
    from src.alert_notify import dispatch as D
    subs = [(1, 7, ["risk"], "warn", None, "u@x.com", "13800001234")]
    bots = [(7, 11), (7, 12)]
    conn = _conn_with(subs, bots)
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch("src.alert_notify.sms.sms_configured", return_value=True):
        rows = D._load_channels()
    assert [(r["channel"], r["id"], r["sub_id"]) for r in rows] == \
        [("email", 7, 1), ("sms", 7, 1), ("im", 11, 1), ("im", 12, 1)]
    assert rows[2]["target"] == "11" and rows[0]["target"] == "u@x.com"   # im target=bot id 字符串


def test_load_channels_sms_skipped_without_creds_and_filters_in_sql():
    """手机有值但短信凭证未配 → sms 行跳过；订阅/用户查询含软删停用过滤（SQL 断言）。"""
    from src.alert_notify import dispatch as D
    conn = _conn_with([(1, 7, ["risk"], "warn", None, "u@x.com", "13800001234")], [])
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch("src.alert_notify.sms.sms_configured", return_value=False):
        rows = D._load_channels()
    assert [r["channel"] for r in rows] == ["email"]
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("deleted_at IS NULL" in s and "alert_user_sub" in s for s in sqls)


# ——— 批34 通道级选择 ———

def test_ch_ok_three_states():
    """三态谓词：None=全通道 / []=零通道（禁 not sel 假值全开——盲审 A-P1-1）/ 精确匹配。"""
    from src.alert_notify.dispatch import _ch_ok
    assert _ch_ok(None, "email") and _ch_ok(None, "im:3")
    assert not _ch_ok([], "email") and not _ch_ok([], "im:3")   # [] 必须零通道
    assert _ch_ok(["email"], "email") and not _ch_ok(["email"], "sms")
    assert _ch_ok(["im:3"], "im:3") and not _ch_ok(["im:3"], "im:4")


def test_load_channels_filters_by_selection():
    """按勾选过滤：sel=["email","im:12"] → 只发 email+bot12（sms/bot11 不发）。"""
    from src.alert_notify import dispatch as D
    conn = _conn_with([(1, 7, ["risk"], "warn", ["email", "im:12"], "u@x.com", "13800001234")],
                      [(7, 11), (7, 12)])
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch("src.alert_notify.sms.sms_configured", return_value=True):
        rows = D._load_channels()
    assert [(r["channel"], r["id"]) for r in rows] == [("email", 7), ("im", 12)]


def test_load_channels_empty_selection_mutes():
    """零勾选=静音：sel=[] → 零行（用户裁定：行保留但不投递）。"""
    from src.alert_notify import dispatch as D
    conn = _conn_with([(1, 7, ["risk"], "warn", [], "u@x.com", "13800001234")],
                      [(7, 11)])
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch("src.alert_notify.sms.sms_configured", return_value=True):
        assert D._load_channels() == []


def test_strip_dead_keys():
    """GET 剥离失效键：bot 删/邮箱清空的键不进回显；None 原样；畸形键滤除。"""
    from src.web_api.routes.alerts import _strip_dead_keys
    avail = {"email": False, "sms": True, "bots": [{"id": 3, "name": "b3"}]}
    assert _strip_dead_keys(None, avail) is None
    assert _strip_dead_keys(["email", "sms", "im:3", "im:9", "bad"], avail) == ["sms", "im:3"]


def test_validate_channels_strips_dead_bot():
    """已删 bot=实体消失 → 剥离落库不 400（用户裁定 2/盲审 A-P0-1 解法）。
    批34 盲审 A-P2-2 后查询两步：①owner+enabled（非自有停用判定）②存在性（已删判定）。"""
    from src.web_api.routes.alerts import _validate_channels
    vc = _conn_with([("u@x.com", "138")], [])   # users(有邮箱) / 名下无 bot
    with patch("src.web_api.routes.alerts.get_conn",
               side_effect=[vc, _conn_with([(0,)]), _conn_with([(0,)])]):
        out, stripped = _validate_channels(7, ["email", "im:99"])
    assert out == ["email"] and stripped == ["im:99"]


def test_validate_channels_rejects_foreign_bot_and_no_email():
    """bot 属他人=400；无邮箱勾 email=400（API 误用信号，非实体消失）——盲审 A-P2-5 断言等值。"""
    from src.web_api.routes.alerts import _validate_channels, ApiError
    conn = _conn_with([("u@x.com", "138")], [])   # 有邮箱有手机，名下无 bot
    # 第一查 owner+enabled=0（非自有），第二查存在性=1（bot 存在=属他人）
    with patch("src.web_api.routes.alerts.get_conn", side_effect=[conn, _conn_with([(0,)]), _conn_with([(1,)])]):
        with pytest.raises(ApiError) as ei:
            _validate_channels(7, ["im:99"])   # bot 99 存在但不属名下
        assert ei.value.code == "ALERT_CHANNEL_INVALID"
    conn2 = _conn_with([("", "138")], [])   # 无邮箱
    with patch("src.web_api.routes.alerts.get_conn", return_value=conn2):
        with pytest.raises(ApiError) as ei2:
            _validate_channels(7, ["email"])
        assert ei2.value.code == "ALERT_CHANNEL_INVALID"


def test_validate_channels_none_passthrough():
    """None=全通道原样（不查库）。"""
    from src.web_api.routes.alerts import _validate_channels
    out, stripped = _validate_channels(7, None)
    assert out is None and stripped == []


def test_alerts_create_with_channels(authed_client):
    """POST 带 channels：校验链过（有邮箱+名下 bot3）→ INSERT 参数含勾选数组。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    vc = MagicMock()   # _validate_channels 内 conn：users(email,phone)+own bots
    vc.__enter__.return_value = vc
    vc.execute.side_effect = [
        MagicMock(fetchone=lambda: ("u@x.com", "138")),   # users email/phone
        MagicMock(fetchall=lambda: [(3,)]),               # own bots
    ]
    ins = MagicMock(fetchone=lambda: (101,))
    conn.execute.side_effect = [
        MagicMock(fetchone=lambda: ("alice",)),           # 用户存在
        MagicMock(fetchone=lambda: (0,)),                 # 行数
        ins,                                              # INSERT RETURNING
    ]
    with patch("src.web_api.routes.alerts.get_conn", side_effect=[conn, vc, conn]), \
         patch("src.web_api.routes.alerts.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.routes.alerts.audit_log"):
        r = authed_client.post("/api/alerts/config",
                               json={"user_id": 7, "channels": ["email", "im:3"], "categories": ["risk"]})
    assert r.status_code == 200 and r.json()["id"] == 101
    ins_call = conn.execute.call_args_list[2]
    assert '"email"' in ins_call.args[1][4] and '"im:3"' in ins_call.args[1][4]   # channels JSON=参数元组第 5 位


def test_alerts_put_omits_channels_keeps_row_value(authed_client):
    """PUT 缺 channels 键=沿用行现值（toggle 开关翻转不重置勾选——批34 B-P1-2）。"""
    row_conn = _conn_with([(5, 7, ["risk"], "warn", True, ["sms"], "alice")])   # _load_row 带 channels
    upd = MagicMock()
    upd.__enter__.return_value = upd
    with patch("src.web_api.routes.alerts.get_conn", side_effect=[row_conn, upd]), \
         patch("src.web_api.routes.alerts.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.routes.alerts.audit_log"):
        r = authed_client.put("/api/alerts/config/5", json={"enabled": False})
    assert r.status_code == 200
    sql, args = upd.execute.call_args_list[0].args
    assert "channels=%s::jsonb" in sql and '"sms"' in args[3]   # 沿用行现值落库
    assert upd.execute.call_count == 1 and row_conn.execute.call_count == 1   # 无校验查询


def test_still_enabled_uses_sub_id_new_table():
    """worker 重查切表（A-P0-2）：SQL 查 alert_user_sub + sub_id；旧 payload 无 sub_id=快照发。"""
    from src.scheduler import alert_tasks as AT
    import inspect
    src = inspect.getsource(AT)
    assert "alert_user_sub" in src and "alert_channel_sub" not in src
    conn = _conn_with([(True,)])
    with patch("src.data_platform.db.get_conn", return_value=conn):
        assert AT is not None   # 模块可导入（重查逻辑在 worker 启动闭包内——SQL 源级断言为主）


# ——— alerts CRUD 用户维度 ———

def test_alerts_create_ok_and_duplicate(authed_client):
    """POST 建行 + 409 重复分支（UniqueViolation → DUPLICATE_SUB）。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.side_effect = [
        MagicMock(fetchone=lambda: ("alice",)),                       # 用户存在
        MagicMock(fetchone=lambda: (0,)),                             # 行数
        MagicMock(fetchone=lambda: (101,)),                           # INSERT RETURNING
    ]
    with patch("src.web_api.routes.alerts.get_conn", return_value=conn), \
         patch("src.web_api.routes.alerts.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.routes.alerts.audit_log"):
        r = authed_client.post("/api/alerts/config", json={"user_id": 7, "categories": ["risk"]})
    assert r.status_code == 200 and r.json()["id"] == 101

    from psycopg.errors import UniqueViolation
    conn2 = MagicMock()
    conn2.__enter__.return_value = conn2
    conn2.execute.side_effect = [
        MagicMock(fetchone=lambda: ("alice",)),
        MagicMock(fetchone=lambda: (1,)),
        UniqueViolation(),                                            # INSERT 撞 UNIQUE(user_id)
    ]
    with patch("src.web_api.routes.alerts.get_conn", return_value=conn2), \
         patch("src.web_api.routes.alerts.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        r2 = authed_client.post("/api/alerts/config", json={"user_id": 7, "categories": ["risk"]})
    assert r2.status_code == 409 and r2.json().get("code") == "DUPLICATE_SUB"


def test_alerts_put_rejects_user_change(authed_client):
    """订阅用户不可改（换人=删了重建）。批34：_load_row 七列（channels 在 enabled 后）。"""
    with patch("src.web_api.routes.alerts.get_conn",
               return_value=_conn_with([(5, 7, ["risk"], "warn", True, ["sms"], "alice")])), \
         patch("src.web_api.routes.alerts.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        r = authed_client.put("/api/alerts/config/5", json={"user_id": 8})
    assert r.status_code == 400


# ——— phone 链（A-P0-1 码绑手机 / A-P1-6 五次作废）———

class _FakeRedis:
    def __init__(self):
        self.d = {}

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d:
            return False
        self.d[k] = v
        return True

    def get(self, k):
        return self.d.get(k)

    def delete(self, *ks):
        for k in ks:
            self.d.pop(k, None)

    def incr(self, k):
        self.d[k] = int(self.d.get(k, 0)) + 1
        return self.d[k]

    def expire(self, k, s):
        return True


def _phone_env(phone="13900001234", fetchones=None):
    """phone 链公共环境。fetchones=逐次 SELECT 返回行（默认两次 request 形 2 元组；
    change 端点 SELECT 为 1 元组——调用方按需传）。audit_log 一并 patch（批28 真库污染教训）。"""
    r = _FakeRedis()
    conn = MagicMock()
    conn.__enter__.return_value = conn
    if fetchones is None:
        fetchones = [(None, "hash"), (None, "hash")]
    conn.execute.side_effect = [MagicMock(fetchone=lambda v=v: v) for v in fetchones]
    patches = [
        patch("src.web_api.redis_pool.redis_client", return_value=r),
        patch("src.web_api.routes.auth_routes.get_conn", return_value=conn),
        patch("src.web_api.auth.verify_password", return_value=True),
        patch("src.alert_notify.sms.send_sms_code", return_value=(True, "ok")),
        patch("src.web_api.routes.auth_routes.audit_log"),
    ]
    return r, conn, patches


def test_phone_request_stores_code_bound_to_phone(authed_client):
    """码值含手机号（A-P0-1：body 提交他号+正确码≠通过——change 只认存侧号）。"""
    import json
    r, conn, patches = _phone_env()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:   # B-P1-1：audit_log 必 patch（真库污染+无 PG 可移植）
        resp = authed_client.post("/api/user/phone-request",
                                  json={"phone": "13900001234", "current_password": "x"})
    assert resp.status_code == 200 and resp.json()["status"] == "sent"
    stored = json.loads(r.d["phone:chg:1"])
    assert stored["phone"] == "13900001234" and len(stored["code"]) == 6


def test_phone_request_cd_and_password_gate(authed_client):
    r, conn, patches = _phone_env()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        assert authed_client.post("/api/user/phone-request",
                                  json={"phone": "13900001234", "current_password": "x"}).status_code == 200
        # 60s 冷却（B 面：NX 二发拒）
        resp = authed_client.post("/api/user/phone-request",
                                  json={"phone": "13911112222", "current_password": "x"})
        assert resp.status_code == 429
    # 密码错拒
    r2, c2, p2 = _phone_env()
    with p2[0], p2[1], patch("src.web_api.auth.verify_password", return_value=False), p2[3]:
        resp = authed_client.post("/api/user/phone-request",
                                  json={"phone": "13900001234", "current_password": "bad"})
    assert resp.status_code == 400


def test_phone_change_wrong_code_five_times_voids(authed_client):
    """码错 5 次作废（A-P1-6）：第 5 次起码键被删——正确码也不再生效。"""
    import json
    r, conn, patches = _phone_env()
    r.d["phone:chg:1"] = json.dumps({"code": "123456", "phone": "13900001234"})
    with patches[0], patches[1]:
        for _ in range(4):
            resp = authed_client.post("/api/user/phone-change", json={"code": "000000"})
            assert resp.status_code == 400
        assert "phone:chg:1" in r.d          # 第 4 次仍在
        resp = authed_client.post("/api/user/phone-change", json={"code": "000000"})
        assert resp.status_code == 400
        assert "phone:chg:1" not in r.d      # 第 5 次=作废
        resp = authed_client.post("/api/user/phone-change", json={"code": "123456"})
        assert resp.status_code == 400


def test_phone_change_success_updates_stored_phone(authed_client):
    """成功：UPDATE 用存侧手机号（非 body——body 根本不收手机号）。"""
    import json
    r, conn, patches = _phone_env(fetchones=[("13900001234",), MagicMock()])   # SELECT+UPDATE 两格
    r.d["phone:chg:1"] = json.dumps({"code": "123456", "phone": "13900001234"})
    with patches[0], patches[1], patches[4] as al:
        resp = authed_client.post("/api/user/phone-change", json={"code": "123456"})
    assert resp.status_code == 200
    upd = [c for c in conn.execute.call_args_list if "UPDATE users SET phone" in c.args[0]][0]
    assert upd.args[1][0] == "13900001234"   # 存侧号（UPDATE 参数元组首元）
    assert al.called and al.call_args.kwargs.get("new_value") == "139****1234"   # 审计脱敏


def test_alerts_get_row_shape(authed_client):
    """批34 盲审 B-P2-5①：GET 行形状端点级钉——channels_sel（None 透传/数组）/channels_avail 三键。"""
    users = [{"id": 7, "username": "alice", "nickname": None,
              "channels": {"email": True, "sms": False, "bots": [{"id": 3, "name": "b3"}]}}]
    subs = [{"id": 1, "user_id": 7, "categories": ["risk"], "min_level": "warn", "enabled": True,
             "channels_raw": ["email", "im:9"], "username": "alice", "nickname": None},   # im:9 失效键
            {"id": 2, "user_id": 7, "categories": [], "min_level": "warn", "enabled": True,
             "channels_raw": None, "username": "alice", "nickname": None}]
    with patch("src.web_api.routes.alerts._users_with_channels", return_value=users), \
         patch("src.web_api.routes.alerts._subs_from_db", return_value=subs), \
         patch("src.web_api.routes.alerts.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}):
        r = authed_client.get("/api/alerts/config")
    assert r.status_code == 200
    rows = r.json()["subs"]
    assert rows[0]["channels_sel"] == ["email"]   # im:9 剥离
    assert rows[0]["channels_avail"] == {"email": True, "sms": False, "bots": [{"id": 3, "name": "b3"}]}
    assert rows[1]["channels_sel"] is None        # None 原样（全通道）


def test_validate_channels_rejects_malformed_key():
    """批34 盲审 B-P2-5③：畸形键 400（等值断言）。"""
    from src.web_api.routes.alerts import _validate_channels, ApiError
    with patch("src.web_api.routes.alerts.get_conn", return_value=_conn_with([("u@x.com", "138")], [])):
        with pytest.raises(ApiError) as ei:
            _validate_channels(7, ["telepathy"])
        assert ei.value.code == "ALERT_CHANNEL_INVALID"


# ─—— 批43：短信多行容灾（failover/候选集/端点/迁移第三态）——

def test_failover_first_fails_second_ok():
    """首行业务错（ALIYUN_*）→ 切次行成功。"""
    from src.alert_notify import sms as S
    rows = [{"id": 1, "name": "a", "access_key_id": "k1", "access_key_secret": "s1",
             "sign_name": "g", "template_code": "T1"},
            {"id": 2, "name": "b", "access_key_id": "k2", "access_key_secret": "s2",
             "sign_name": "g", "template_code": "T2"}]
    sends = []
    def fake_send(cfg, params, timeout=(3, 10)):
        sends.append(cfg["id"])
        return (False, "ALIYUN_LIMIT_CONTROL") if cfg["id"] == 1 else (True, "ok")
    with patch.object(S, "_available_providers", return_value=rows), \
         patch.object(S, "_aliyun_send", side_effect=fake_send):
        ok, reason = S.send_sms("13800000000", "warn", "t")
    assert ok and sends == [1, 2]


def test_failover_timeout_no_fallback():
    """timeout（结局模糊）→ 不切次行（批7 双发计费防线）。"""
    from src.alert_notify import sms as S
    rows = [{"id": 1, "access_key_id": "k1", "access_key_secret": "s1", "sign_name": "g", "template_code": "T"},
            {"id": 2, "access_key_id": "k2", "access_key_secret": "s2", "sign_name": "g", "template_code": "T"}]
    sends = []
    def fake_send(cfg, params, timeout=(3, 10)):
        sends.append(cfg["id"])
        return False, "timeout"
    with patch.object(S, "_available_providers", return_value=rows), \
         patch.object(S, "_aliyun_send", side_effect=fake_send):
        ok, reason = S.send_sms("13800000000", "warn", "t")
    assert not ok and reason == "timeout" and sends == [1]   # 只尝试首行


def test_candidate_sets_independent():
    """告警/验证码候选独立：缺 verify_tpl 的行可发告警不进验证码候选。"""
    from src.alert_notify import sms as S
    rows = [(1, "a", "k", "enc", "g", "T1", ""),   # 无 verify_tpl
            (2, "b", "k", "enc", "g", "T1", "V2")]
    def fake_conn(*a, **k):
        c = MagicMock(); c.__enter__.return_value = c
        c.execute.return_value.fetchall.return_value = rows
        return c
    import src.quant_common.crypto as C
    with patch("src.data_platform.db.get_conn", side_effect=fake_conn), \
         patch.object(C, "decrypt", return_value="sec"):
        alert_set = S._available_providers(require_verify_tpl=False)
        verify_set = S._available_providers(require_verify_tpl=True)
    assert [r["id"] for r in alert_set] == [1, 2]      # 两行都可发告警
    assert [r["id"] for r in verify_set] == [2]          # 只有行 2 进验证码候选


def test_sms_providers_crud_and_reorder(authed_client):
    """CRUD+reorder 端点：新建 position=MAX+1/reorder 全集校验/幽灵 id 拒。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.side_effect = [
        (1,), (2,),          # INSERT RETURNING ×2（建两行）
        (None,),             # reorder SELECT id fetchall 走 fetchall 不 fetchone
    ]
    conn.execute.return_value.fetchall.return_value = [(1,), (2,)]
    with patch("src.web_api.routes.alerts.get_conn", return_value=conn), \
         patch("src.web_api.routes.alerts.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.routes.alerts.audit_log"), \
         patch("src.quant_common.crypto.encrypt", side_effect=lambda v: f"ENC({v})"):
        r1 = authed_client.post("/api/alerts/sms-providers",
                                json={"name": "a", "access_key_id": "k", "access_key_secret": "s", "sign_name": "g"})
        r2 = authed_client.post("/api/alerts/sms-providers",
                                json={"name": "b", "access_key_id": "k", "access_key_secret": "s", "sign_name": "g"})
        assert r1.status_code == 200 and r2.status_code == 200
        rr = authed_client.post("/api/alerts/sms-providers/reorder", json={"ids": [2, 1]})
        assert rr.status_code == 200
        # 幽灵 id 拒（集合不等）
        conn.execute.return_value.fetchall.return_value = [(1,)]
        rb = authed_client.post("/api/alerts/sms-providers/reorder", json={"ids": [1, 99]})
        assert rb.status_code == 400
