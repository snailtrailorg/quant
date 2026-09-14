"""批23 表格与告警设置整顿（后端）：通知/审计批量删除 + tasks 多值状态。

覆盖：①/api/notifications/delete——ids 参数校验三档+可见类别作用域（category=ANY
防越权删不可见类别）+all 仅 admin+audit_log 留痕；②/api/audit/delete 同款；
③task_manager.list_tasks 逗号分隔多值 status（空/缺省=不过滤，单值走 ANY 兼容）。

桩模式同 test_user_mgmt_batch11：verify_jwt 打桩身份层 + 双 patch get_conn
（端点模块级绑定 + 权限层 data_platform.db）+ audit_log 打桩断言。
"""
from unittest.mock import patch, MagicMock

ADMIN = {"sub": "1", "username": "admin", "role": "admin", "db_role": "admin"}
TRADER = {"sub": "2", "username": "trader", "role": "trader", "db_role": "trader"}
VIEWER = {"sub": "3", "username": "viewer", "role": "viewer", "db_role": "viewer"}


def _conn(rowcount=0):
    """可控 conn：execute 统一返回同 cur（权限层查询也走它，fetchall=[] 零权限行）。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = None
    cur.rowcount = rowcount
    conn.execute.return_value = cur
    return conn


def _client():
    from fastapi.testclient import TestClient
    from src.web_api.main import app
    return TestClient(app)


def _post(path, body, module, who=ADMIN, conn=None):
    """POST 打桩三件套：身份（verify_jwt）+DB（端点模块级绑定+权限层定义处）+审计。

    module=端点所在路由模块名（notifications 在 system、audit 在 risk）。
    """
    with patch("src.web_api.auth.verify_jwt", return_value=who), \
         patch(f"src.web_api.routes.{module}.get_conn", return_value=conn or _conn()), \
         patch("src.data_platform.db.get_conn", return_value=conn or _conn()), \
         patch(f"src.web_api.routes.{module}.audit_log") as au:
        r = _client().post(path, json=body, headers={"Authorization": "Bearer t"})
    return r, au, conn


class TestNotificationsDelete:
    """POST /api/notifications/delete（批23）。"""

    def test_ids_mode_sql_and_audit(self):
        """ids 模式：真删计数 + SQL 带类别作用域与 id 双条件 + 留痕。"""
        from src.alert_notify.notify import visible_categories
        conn = _conn(rowcount=3)
        r, au, _ = _post("/api/notifications/delete", {"ids": [1, 2, 3]}, "system", conn=conn)
        assert r.status_code == 200 and r.json() == {"deleted": 3}
        sql, params = conn.execute.call_args[0]
        assert "DELETE FROM notifications" in sql
        assert "category = ANY(%s)" in sql and "id = ANY(%s)" in sql   # 作用域+选中双条件
        assert params[0] == visible_categories("admin") and params[1] == [1, 2, 3]
        assert au.call_args[0][:2] == ("admin", "notifications_delete")   # 删必留痕
        assert "n=3" in au.call_args[0][2]

    def test_category_scope_non_admin(self):
        """非 admin 删除：ANY 列表=其可见类别（不含 email）——越权类别进不了删除面。
        user_mgmt 恒 admin（锁键）下 HTTP 层进不来，直调函数测作用域防线本体。"""
        from src.alert_notify.notify import visible_categories
        from src.web_api.routes.system import notifications_delete
        conn = _conn(rowcount=1)
        with patch("src.web_api.routes.system.get_conn", return_value=conn):
            out = notifications_delete({"ids": [5]}, payload=TRADER)
        assert out == {"deleted": 1}
        cats = conn.execute.call_args[0][1][0]
        assert cats == visible_categories("trader") and "email" not in cats

    def test_all_admin_only(self):
        """all=true 非 admin 拒。双防线：HTTP 层 trader 无 user_mgmt→require_perm 403；
        函数直调（payload 非 admin）→ ADMIN_ONLY（user_mgmt 恒 admin 下仍留纵深防御）。"""
        import pytest
        from src.web_api.errors import ApiError
        from src.web_api.routes.system import notifications_delete
        r, au, _ = _post("/api/notifications/delete", {"all": True}, "system", who=TRADER, conn=_conn())
        assert r.status_code == 403   # require_perm 层先拦（trader 无 user_mgmt）
        au.assert_not_called()
        with patch("src.web_api.routes.system.get_conn", return_value=_conn()), \
             patch("src.web_api.routes.system.audit_log") as au2:
            with pytest.raises(ApiError) as ei:
                notifications_delete({"all": True}, payload=TRADER)
        assert ei.value.status_code == 403 and ei.value.code == "ADMIN_ONLY"
        au2.assert_not_called()

    def test_all_admin_wipes_visible(self):
        """admin all=true：只按类别作用域全清（无 id 条件）。"""
        conn = _conn(rowcount=9)
        r, _, _ = _post("/api/notifications/delete", {"all": True}, "system", conn=conn)
        assert r.status_code == 200 and r.json() == {"deleted": 9}
        sql = conn.execute.call_args[0][0]
        assert "category = ANY(%s)" in sql and "id = ANY" not in sql

    def test_ids_validation(self):
        """三档校验：空/非整型/超 100。"""
        for body, code in (({"ids": []}, "IDS_EMPTY"),
                           ({"ids": ["a"]}, "IDS_INVALID"),
                           ({"ids": list(range(101))}, "TOO_MANY")):
            r, _, _ = _post("/api/notifications/delete", body, "system", conn=_conn())
            assert r.status_code == 400 and r.json()["code"] == code, body

    def test_viewer_forbidden(self):
        """viewer 无 user_mgmt：require_perm 层 403。"""
        r, _, _ = _post("/api/notifications/delete", {"ids": [1]}, "system", who=VIEWER, conn=_conn())
        assert r.status_code == 403


class TestAuditDelete:
    """POST /api/audit/delete（批23）：audit 自删尤其留痕。"""

    def test_ids_mode_and_audit(self):
        conn = _conn(rowcount=2)
        r, au, _ = _post("/api/audit/delete", {"ids": [10, 20]}, "risk", conn=conn)
        assert r.status_code == 200 and r.json() == {"deleted": 2}
        sql, params = conn.execute.call_args[0]
        assert "DELETE FROM audit_log" in sql and "id = ANY(%s)" in sql
        assert params == ([10, 20],)
        assert au.call_args[0][:2] == ("admin", "audit_delete")   # 自删留痕
        assert "n=2" in au.call_args[0][2]

    def test_all_admin_only(self):
        """all=true 非 admin 拒；admin 全清（无 id 条件）。双防线同通知端点。"""
        import pytest
        from src.web_api.errors import ApiError
        from src.web_api.routes.risk import audit_delete
        r, au, _ = _post("/api/audit/delete", {"all": True}, "risk", who=TRADER, conn=_conn())
        assert r.status_code == 403
        au.assert_not_called()
        with pytest.raises(ApiError) as ei:
            audit_delete({"all": True}, payload=TRADER)
        assert ei.value.status_code == 403 and ei.value.code == "ADMIN_ONLY"
        conn = _conn(rowcount=7)
        r, _, _ = _post("/api/audit/delete", {"all": True}, "risk", conn=conn)
        assert r.status_code == 200 and r.json() == {"deleted": 7}
        assert conn.execute.call_args[0][0].strip() == "DELETE FROM audit_log"

    def test_empty_ids(self):
        r, _, _ = _post("/api/audit/delete", {"ids": []}, "risk", conn=_conn())
        assert r.status_code == 400 and r.json()["code"] == "IDS_EMPTY"

    def test_viewer_forbidden(self):
        r, _, _ = _post("/api/audit/delete", {"ids": [1]}, "risk", who=VIEWER, conn=_conn())
        assert r.status_code == 403



class TestListTasksMultiStatus:
    """list_tasks 多值 status（批23 A-P1-5：行筛选多选走后端，防稀疏状态被 LIMIT 挤出）。"""

    def _call(self, status):
        conn = _conn()
        with patch("src.task_manager.get_conn", return_value=conn):
            from src.task_manager import list_tasks
            items = list_tasks(status=status, limit=100)
        return items, conn

    def test_multi_status_any(self):
        """逗号分隔多值 → status = ANY(%s)，strip 去空格。"""
        items, conn = self._call("running, pending")
        assert items == []
        sql, params = conn.execute.call_args[0]
        assert "status = ANY(%s)" in sql and params == (["running", "pending"], 100)

    def test_single_value_compat(self):
        """单值也走 ANY（等价旧 =%s，保持兼容）。"""
        _, conn = self._call("running")
        assert "status = ANY(%s)" in conn.execute.call_args[0][0]
        assert conn.execute.call_args[0][1] == (["running"], 100)

    def test_empty_no_filter(self):
        """空/缺省=不过滤（无 WHERE，保持现状兼容）。"""
        for s in (None, "", "  ,  "):
            _, conn = self._call(s)
            assert "WHERE" not in conn.execute.call_args[0][0], s
