"""发件箱指数退避+批47 SMTP 多通道 failover 状态机+smtp_provider 端点钉。

批47 改写史：原 _smtp_config/MAX_ATTEMPTS 三测随函数退役（盲审 B-P1-1）——_providers 形态
重写；backoff 曲线两测原样保留（同实例退避节奏不变）。
"""
from unittest.mock import patch, MagicMock, call

import pytest

from src.email_service import _backoff_seconds, SWEEP_ABS_LIMIT


@pytest.fixture
def client():
    from src.web_api.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    from src.web_api import auth as _auth
    with patch.object(_auth, "verify_jwt",
                      return_value={"sub": 1, "username": "u1", "role": "admin", "db_role": "admin"}):
        client.headers.update({"Authorization": "Bearer test-token"})
        yield client


def _conn_seq(*cursors):
    """get_conn 逐次返回的 conn 序列（patch side_effect 用；每个 conn 的 execute 返回对应 cursor）。"""
    conns = []
    for cur in cursors:
        c = MagicMock()
        c.__enter__.return_value = c
        c.execute.return_value = cur
        conns.append(c)
    return conns


def _cur(fetchone=None, fetchall=None):
    m = MagicMock()
    m.fetchone.return_value = fetchone
    m.fetchall.return_value = fetchall or []
    return m


# ——— 退避曲线（既有，同实例重试节奏不变）———

def test_backoff_curve():
    assert _backoff_seconds(1) == 60        # 1 分钟
    assert _backoff_seconds(2) == 120       # 2 分钟
    assert _backoff_seconds(3) == 240       # 4 分钟
    assert _backoff_seconds(4) == 480       # 8 分钟
    assert _backoff_seconds(5) == 960       # 16 分钟


def test_backoff_cap_30min():
    assert _backoff_seconds(6) == 1800      # 30 分钟封顶
    assert _backoff_seconds(100) == 1800


# ——— 批47：配额/_providers/底层发送 ———

def test_max_attempts_quota():
    """配额读 system_config smtp_max_attempts（缺省 3；**总尝试=配额**——用户裁定 A）。"""
    from src.email_service import _max_attempts
    with patch("src.data_platform.db.get_conn", side_effect=_conn_seq(_cur(fetchone=("3",)))):
        assert _max_attempts() == 3
    with patch("src.data_platform.db.get_conn", side_effect=_conn_seq(_cur(fetchone=("7",)))):
        assert _max_attempts() == 7
    with patch("src.data_platform.db.get_conn", side_effect=_conn_seq(_cur(fetchone=None))):
        assert _max_attempts() == 3   # 未配置回落


def test_providers_position_order_decrypt_skip():
    """_providers：position 序+密码解密+username 空跳过+auto 端口推断（465→ssl/587→starttls）。"""
    from src.email_service import _providers
    rows = [(2, "h2", 465, "auto", "u2", "ENC2", ""),      # position 序第二条（465→ssl）
            (1, "h1", 587, "auto", "u1", "ENC1", "  "),    # 第一条（from 空白→回落 username）
            (3, "h3", 25, "auto", "", "ENC3", "")]         # username 空=未配置跳过
    with patch("src.data_platform.db.get_conn", side_effect=_conn_seq(_cur(fetchall=rows))), \
         patch("src.quant_common.crypto.decrypt", return_value="decrypted"):
        ps = _providers()
    assert [p["id"] for p in ps] == [2, 1]   # DB ORDER BY 保证序（mock 只验解析）：3 号 username 空跳过
    assert ps[0]["security"] == "ssl" and ps[0]["port"] == 465
    assert ps[1] == {"id": 1, "host": "h1", "port": 587, "security": "starttls",
                     "username": "u1", "password": "decrypted", "from": "u1"}   # from 空白→回落 username


def test_send_email_sync_unconfigured_dev_mode():
    """无通道：未开 SMTP_DEV=失败；DEV 打印=成功（本地开发）。"""
    import os
    from src.email_service import _send_email_sync
    with patch.dict(os.environ, {"SMTP_DEV": ""}, clear=False):
        assert "未配置" in _send_email_sync("a@b.c", "s", "b", None)
    with patch.dict(os.environ, {"SMTP_DEV": "true"}, clear=False):
        assert _send_email_sync("a@b.c", "s", "b", None) is None


def test_send_email_sync_ssl_vs_starttls():
    """provider dict 形态：465=隐式 SSL；587=STARTTLS（security 已由 _providers 解析）。"""
    import smtplib
    from src.email_service import _send_email_sync
    with patch("src.email_service.smtplib.SMTP_SSL") as ssl_cls, \
         patch("src.email_service.smtplib.SMTP") as plain_cls:
        _send_email_sync("a@b.c", "s", "<p>x</p>",
                         {"id": 1, "host": "h", "port": 465, "security": "ssl",
                          "username": "u", "password": "p", "from": "f@x.com"})
    ssl_cls.assert_called_once_with("h", 465, timeout=60)
    plain_cls.assert_not_called()
    with patch("src.email_service.smtplib.SMTP_SSL") as ssl_cls2, \
         patch("src.email_service.smtplib.SMTP") as plain_cls2:
        _send_email_sync("a@b.c", "s", "<p>x</p>",
                         {"id": 1, "host": "h", "port": 587, "security": "starttls",
                          "username": "u", "password": "p", "from": "f@x.com"})
    plain_cls2.assert_called_once_with("h", 587, timeout=60)
    plain_cls2.return_value.__enter__.return_value.starttls.assert_called_once()


# ——— 批47：_try_row_sync failover 状态机（claim 两 conn+providers/quota/send mock）———

_PROV = [{"id": 1, "host": "h1", "port": 587, "security": "starttls", "username": "u1",
          "password": "p", "from": "f1"},
         {"id": 2, "host": "h2", "port": 465, "security": "ssl", "username": "u2",
          "password": "p", "from": "f2"}]


def _run_try_row(row, send_err, providers=None, quota=3):
    """组装 _try_row_sync 依赖并跑（row=claim 返回行元组）。返回回写 conn（断言 UPDATE 用）。"""
    from src.email_service import _try_row_sync
    claim_cur = _cur(fetchone=row)
    conns = _conn_seq(claim_cur, _cur())   # claim conn / 回写 conn
    with patch("src.email_service._providers", return_value=providers if providers is not None else _PROV), \
         patch("src.email_service._max_attempts", return_value=quota), \
         patch("src.email_service._send_email_sync", return_value=send_err), \
         patch("src.data_platform.db.get_conn", side_effect=conns), \
         patch("src.data_platform.log_sink.event"), \
         patch("src.email_service._final_failure_notify") as fn:
        _try_row_sync(99)
    return conns[1], fn


def test_try_row_success_marks_sent():
    row = (99, "a@b.c", "s", "b", 0, None, 0)
    write_cur, fn = _run_try_row(row, None)
    sql = write_cur.execute.call_args[0][0]
    assert "status='sent'" in sql and "AND status='sending'" in sql   # 批27-1④ 防双写保留
    fn.assert_not_called()


def test_try_row_retry_same_provider():
    """失败且未达配额 → 同实例退避重试（provider_attempts 累加，provider_id 不变）。
    批47 盲审 A-P1-1 钉：退避基数=通道内计数（切通道后首败 60s 而非全局累计的 8min）。"""
    row = (99, "a@b.c", "s", "b", 0, 1, 0)   # 已在实例 1 试 0 次
    write_cur, fn = _run_try_row(row, "boom")
    sql, args = write_cur.execute.call_args[0]
    assert "status='pending'" in sql and "provider_attempts=%s" in sql
    assert args == (1, 1, 60, "boom", 99)     # attempts=1, provider_attempts=1, 退避 60s
    fn.assert_not_called()
    # 切到实例 2 后首败（attempts 全局=3 但 prov_att=0）→ 退避 60s（非 _backoff_seconds(4)=480）
    row2 = (99, "a@b.c", "s", "b", 3, 2, 0)
    write_cur2, _ = _run_try_row(row2, "boom")
    assert write_cur2.execute.call_args[0][1][2] == 60


def test_try_row_switch_next_provider():
    """配额尽（总尝试=配额——裁定 A）→ 切下一实例：provider_id 变更+清零+60s 短退避。"""
    row = (99, "a@b.c", "s", "b", 2, 1, 2)   # 实例 1 已试 2 次，quota=3 → 2+1=3 达配额
    write_cur, fn = _run_try_row(row, "boom", quota=3)
    sql, args = write_cur.execute.call_args[0]
    assert "status='pending'" in sql and "provider_id=%s" in sql and "provider_attempts=0" in sql
    assert "interval '60 seconds'" in sql
    assert args == (3, 2, "boom", 99)         # attempts=3, 切到实例 2
    fn.assert_not_called()


def test_try_row_all_exhausted_failed():
    """最后实例配额尽（轮转一圈）→ failed 终态+最终失败通知（防递归：dispatch 侧跳 email）。"""
    row = (99, "a@b.c", "s", "b", 8, 2, 2)   # 已在实例 2（最后）试 2 次
    write_cur, fn = _run_try_row(row, "boom", quota=3)
    sql, args = write_cur.execute.call_args[0]
    assert "status='failed'" in sql
    assert args == (9, "boom", 99)
    fn.assert_called_once()


def test_try_row_dangling_provider_falls_back_first():
    """provider_id 悬空（实例删/停用）→ 回落第一实例（盲审 A-P1-2）。"""
    row = (99, "a@b.c", "s", "b", 0, 777, 0)   # 777 不在 providers
    write_cur, fn = _run_try_row(row, "boom")
    sql, args = write_cur.execute.call_args[0]
    assert "provider_attempts=%s" in sql and args[0] == 1   # 同实例（=第一实例）重试分支


def test_try_row_no_provider_failed_directly():
    """无通道（未配置）→ 无重试意义，直接 failed（配置问题非瞬时故障）。"""
    row = (99, "a@b.c", "s", "b", 0, None, 0)
    write_cur, fn = _run_try_row(row, "SMTP 未配置", providers=[])
    sql, _ = write_cur.execute.call_args[0]
    assert "status='failed'" in sql
    fn.assert_called_once()


# ——— 批47：smtp_provider 端点（批43 test_batch30:456 同款）———

def _auth(client, method, url, **kw):
    with patch("src.web_api.routes.system.require_perm",
               return_value={"sub": 1, "username": "admin", "db_role": "admin"}), \
         patch("src.web_api.routes.system.audit_log"):
        return getattr(client, method)(url, **kw)


def test_smtp_providers_crud_reorder(authed_client):
    """CRUD+reorder 全集校验+幽灵 id 拒+旧端点 404（批47 退役）。"""
    from src.web_api.routes.system import _smtp_provider_row

    # GET 空表
    with patch("src.web_api.routes.system.get_conn",
               side_effect=_conn_seq(_cur(fetchall=[]))):
        r = _auth(authed_client, "get", "/api/smtp-providers")
    assert r.status_code == 200 and r.json()["items"] == []

    # POST 建（host/username/password 必填校验）
    r = _auth(authed_client, "post", "/api/smtp-providers",
              json={"name": "主", "host": "", "username": "u", "password": "p"})
    assert r.status_code == 400

    # PUT 编辑（密码留空=不改——批38 三段语义：缺键路径）
    with patch("src.web_api.routes.system.get_conn",
               side_effect=_conn_seq(_cur(fetchone=(1,)), _cur())), \
         patch("src.quant_common.crypto.encrypt", return_value="ENC"):
        r = _auth(authed_client, "post", "/api/smtp-providers/1",
                  json={"name": "新名", "host": "h", "port": 465, "security": "ssl", "enabled": False})
    assert r.status_code == 200

    # reorder：幽灵 id 拒（全集校验）
    with patch("src.web_api.routes.system.get_conn",
               side_effect=_conn_seq(_cur(fetchall=[(1,), (2,)]))):
        r = _auth(authed_client, "post", "/api/smtp-providers/reorder", json={"ids": [1, 999]})
    assert r.status_code == 400

    # 旧端点 404（退役）
    assert _auth(authed_client, "get", "/api/smtp-config").status_code == 404


def test_smtp_row_shape_password_not_leaked():
    from src.web_api.routes.system import _smtp_provider_row
    r = _smtp_provider_row((1, "n", "v", "h", 465, "ssl", "u", "SECRET", "f", 0, True))   # 批53 追加 vendor 列
    assert r["password_set"] is True and "password" not in r
