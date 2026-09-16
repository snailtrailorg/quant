"""批32 · 表格撑满+日志懒加载单测（docs/任务/批32-表格撑满与日志懒加载.md v2 契约）。

覆盖：
- /api/log：游标 SQL 双键排序（ts DESC, id DESC——log_sink 同事务批量同 ts 单键必丢/重行）
  + (ts,id) 谓词参数 + level/module ANY + 首屏 modules 附带/翻页不带 + next 游标（满页/不满页）
- 游标解析失败 400 CURSOR_INVALID
- /api/audit：形状 list→dict（logs/next/actors/actions）+ actor/action ANY + 双键排序
- mock：SQL 前缀分派（test_batch25 _cfg_conn 先例——主查询与 DISTINCT 交错污染防护）
"""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

ADMIN = {"sub": 1, "username": "admin", "role": "admin", "db_role": "admin"}


@pytest.fixture(scope="module")
def client():
    from src.web_api.main import app
    return TestClient(app)


def _dispatch(handlers):
    """SQL 前缀分派 mock（批32 B-P2-1 先例）。handlers=[(子串, fetchall 行), ...] 按序首中。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn

    def _exec(sql, *a, **kw):
        for pat, rows in handlers:
            if pat in sql:
                cur = MagicMock()
                cur.fetchall.return_value = rows
                return cur
        raise AssertionError(f"unexpected SQL: {sql[:80]}")
    conn.execute = MagicMock(side_effect=_exec)
    return conn


def _ts(sec=1700000000):
    return datetime.fromtimestamp(sec, tz=timezone.utc)


_LOG_PAGE = [(i, "INFO", "email", f"m{i}", _ts(1700000000 - i)) for i in range(1, 3)]   # 2 行


def _log_conn(page=_LOG_PAGE, mods=("email", "im")):
    return _dispatch([
        ("SELECT DISTINCT module", [(m,) for m in mods]),
        ("FROM system_log", page),
    ])


def test_log_first_page_dual_key_and_modules(client):
    """首屏：双键排序 + modules 下拉源附带。"""
    c = _log_conn()
    with patch("src.web_api.routes.auth_routes.get_conn", return_value=c), \
         patch("src.web_api.auth.verify_jwt", return_value=ADMIN):
        r = client.get("/api/log?limit=2", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200
    d = r.json()
    assert "ORDER BY ts DESC, id DESC" in c.execute.call_args_list[0].args[0]
    assert d["modules"] == ["email", "im"]
    assert d["next"] and "|" in d["next"]   # 满页=可能还有


def test_log_short_page_next_null(client):
    c = _log_conn()
    with patch("src.web_api.routes.auth_routes.get_conn", return_value=c), \
         patch("src.web_api.auth.verify_jwt", return_value=ADMIN):
        r = client.get("/api/log?limit=5", headers={"Authorization": "Bearer t"})
    assert r.json()["next"] is None   # 2 行 < 5=终页


def test_log_cursor_predicate_and_filters(client):
    """before 游标 → (ts,id) 谓词 + 全精度解析；level/module 重复参数 → ANY。"""
    c = _log_conn()
    before = f"{int(_ts(1700000000).timestamp() * 1_000_000)}|99"
    with patch("src.web_api.routes.auth_routes.get_conn", return_value=c), \
         patch("src.web_api.auth.verify_jwt", return_value=ADMIN):
        r = client.get(f"/api/log?limit=2&before={before}&level=ERROR&level=WARN&module=email",
                       headers={"Authorization": "Bearer t"})
    assert r.status_code == 200
    sql = c.execute.call_args_list[0].args[0]
    args = c.execute.call_args_list[0].args[1]
    assert "(ts, id) < (%s, %s)" in sql and "level = ANY(%s)" in sql and "module = ANY(%s)" in sql
    assert args[1] == 99 and args[2] == ["ERROR", "WARN"] and args[3] == ["email"]


def test_log_cursor_invalid_400(client):
    with patch("src.web_api.auth.verify_jwt", return_value=ADMIN):
        r = client.get("/api/log?before=garbage", headers={"Authorization": "Bearer t"})
    assert r.status_code == 400 and r.json().get("code") == "CURSOR_INVALID"


def test_audit_shape_and_filters(client):
    """audit：list→dict 形状 + actor/action ANY + 双键 + 首屏 actors/actions（LIMIT 500 封顶）。"""
    page = [(1, _ts(1700000000), "admin", "login", None, "1.2.3.4"),
            (2, _ts(1699999999), "u1", "phone_change", "u1", "139****")]
    c = _dispatch([
        ("SELECT DISTINCT actor", [("admin",), ("u1",)]),
        ("SELECT DISTINCT action", [("login",), ("phone_change",)]),
        ("FROM audit_log", page),
    ])
    with patch("src.web_api.routes.risk.get_conn", return_value=c), \
         patch("src.web_api.auth.verify_jwt", return_value=ADMIN):
        r = client.get("/api/audit?limit=2&actor=admin&actor=u1&action=login",
                       headers={"Authorization": "Bearer t"})
    assert r.status_code == 200
    d = r.json()
    assert set(d.keys()) >= {"logs", "next", "actors", "actions"}
    assert d["actors"] == ["admin", "u1"] and d["actions"] == ["login", "phone_change"]
    assert d["next"]   # 满页
    sql = c.execute.call_args_list[0].args[0]
    args = c.execute.call_args_list[0].args[1]
    assert "ORDER BY ts DESC, id DESC" in sql and "actor = ANY(%s)" in sql and "action = ANY(%s)" in sql
    assert args[0] == ["admin", "u1"] and args[1] == ["login"]


def test_log_modules_not_sent_on_pagination(client):
    """翻页（带 before）不带 modules（A-P2-3：值域不变不重跑）。"""
    c = _dispatch([("FROM system_log", _LOG_PAGE)])   # 无 DISTINCT handler——出现即 AssertionError
    before = f"{int(_ts(1700000000).timestamp() * 1_000_000)}|99"
    with patch("src.web_api.routes.auth_routes.get_conn", return_value=c), \
         patch("src.web_api.auth.verify_jwt", return_value=ADMIN):
        r = client.get(f"/api/log?limit=2&before={before}", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and r.json()["modules"] == []
    assert len(c.execute.call_args_list) == 1   # A-P1-3：真钉——DISTINCT 若被调用会触发 handler AssertionError
    assert len(r.json()["logs"]) == 2   # 行未被连坐清空（宽 except 吞 Assertion 的反证）
