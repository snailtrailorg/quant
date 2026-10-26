# -*- coding: utf-8 -*-
"""批 153 · 安全快照默认查询回落最近一行——三钉。

每日 00:00-06:00 今日无行的语义空窗（前端恒显「暂无」6 小时）——默认查询回落
最近一行；显式 ?date= 查无仍 404 保真。
"""
from datetime import date
from unittest.mock import MagicMock, patch

from src.web_api.routes import security as sec


def _snap(d):
    return {"scan_date": d, "status": "ok", "components": {}, "ports": {},
            "ssh_cfg": {}, "dnf": {}, "headers": {}, "ai_summary": "",
            "created_at": None}


class TestFallback:
    def test_today_missing_falls_back_latest(self):
        """今日无行 → 回落最近行，响应 scan_date=实际快照日（防错报今日）。"""
        yesterday = date(2026, 10, 26)
        with patch.object(sec.store, "get_snapshot", return_value=None), \
             patch.object(sec.store, "get_latest_snapshot",
                          return_value=_snap(yesterday)), \
             patch.object(sec, "_enrich_findings", return_value=[]), \
             patch.object(sec.store, "get_prev_snapshot", return_value=None), \
             patch.object(sec.store, "get_component_ledger", return_value=[]), \
             patch.object(sec, "_baseline_diff_safe", return_value=[]), \
             patch.object(sec, "require_perm", lambda k: lambda: None, create=True):
            resp = sec.security_snapshot(date_str="")
        assert resp["scan_date"] == "2026-10-26"

    def test_explicit_date_missing_still_404(self):
        """显式 ?date= 查无 → 404 保真（不回落——指定日期没扫就是没扫）。"""
        import pytest
        from src.web_api.errors import ApiError
        with patch.object(sec.store, "get_snapshot", return_value=None):
            with pytest.raises(ApiError) as ei:
                sec.security_snapshot(date_str="20261020")
        assert ei.value.status_code == 404

    def test_all_empty_404(self):
        """全表空（首部署）→ 404（前端「首次采集后展示」文案本意）。"""
        import pytest
        from src.web_api.errors import ApiError
        with patch.object(sec.store, "get_snapshot", return_value=None), \
             patch.object(sec.store, "get_latest_snapshot", return_value=None):
            with pytest.raises(ApiError):
                sec.security_snapshot(date_str="")
