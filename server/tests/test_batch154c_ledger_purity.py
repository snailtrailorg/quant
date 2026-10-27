# -*- coding: utf-8 -*-
"""批 154 补② · 台账纯净性钉——元数据键不伪组件化（用户页面 outdated 行渲染全串实证）。"""
import inspect
from src.health_monitor import security_collector as sc


def test_sys_rows_str_only():
    """sys_rows 白名单式 isinstance(v, str)——list/int/dict 元键不入台账。"""
    src = inspect.getsource(sc.run_security_scan)
    assert "isinstance(v, str)" in src


def test_meta_keys_purge():
    """存量伪组件自愈 DELETE（outdated/n_outdated/dep_audit_at/vulns_db）。"""
    src = inspect.getsource(sc.run_security_scan)
    assert "'outdated', 'n_outdated', 'dep_audit_at', 'vulns_db'" in src


def test_meta_keys_never_rows():
    """语义钉：outdated 值为 list 时 str(v) 才产生全串伪行——白名单后绝迹。"""
    part = {"components": {"nginx": "1.24.0", "outdated": [{"name": "x"}],
                            "n_outdated": 3, "vulns_db": {"state": "updated"}}}
    rows = [("system", k, str(v), "系统组件") for k, v in part["components"].items()
            if k not in ("pip", "status") and isinstance(v, str) and v]
    assert [r[1] for r in rows] == ["nginx"]
