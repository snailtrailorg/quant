"""批 140：AI 历史摘要弹窗后端（DELETE /api/security/summary + history 全文摘要）。"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class TestSummaryDelete:
    def test_delete_ok(self):
        """删中 ⇒ ok + 清 history 缓存 + 审计。"""
        from src.web_api.routes import security as S
        with patch.object(S.store, "delete_snapshot", return_value=True) as ds, \
                patch.object(S, "_SECURITY_HISTORY_CACHE", {}) as cache, \
                patch("src.data_platform.audit.audit_log") as al:
            out = S.security_summary_delete("20261021", {"username": "admin"})
        assert out == {"ok": True}
        assert ds.call_args[0][0] == date(2026, 10, 21)
        assert len(cache) == 0
        assert al.called

    def test_delete_404_when_absent(self):
        from src.web_api.routes import security as S
        from src.web_api.errors import ApiError
        with patch.object(S.store, "delete_snapshot", return_value=False), \
                pytest.raises(ApiError) as ei:
            S.security_summary_delete("20200101", {"username": "admin"})
        assert ei.value.code == "SECURITY_SUMMARY_NOT_FOUND"

    def test_route_depends_wrapped_and_no_sql(self):
        import inspect
        from src.web_api.routes.security import security_summary_delete
        src = inspect.getsource(security_summary_delete)
        assert "Depends(require_perm" in src
        assert "SELECT " not in src and "DELETE FROM" not in src  # SQL 在 store 层

    def test_history_carries_full_summary(self):
        """history 行必须带全文 ai_summary（弹窗表格的「摘要」列数据面）。"""
        from src.web_api.routes import security as S
        import src.data_platform.security_store as store
        day = date(2026, 10, 21)
        snap_row = {"scan_date": day, "status": "ok", "components": {}, "ports": [],
                    "ssh_cfg": {}, "ai_summary": "第一行\n第二行"}
        with patch.object(store, "get_snapshots_recent", return_value=[snap_row]), \
                patch.object(store, "get_finding_spans", return_value=[]), \
                patch.object(S, "_SECURITY_HISTORY_CACHE", {}):
            out = S.security_history(days=7, _u={"username": "u"})
        assert out["items"][0]["ai_summary"] == "第一行\n第二行"
        assert out["items"][0]["ai_summary_preview"] == "第一行"