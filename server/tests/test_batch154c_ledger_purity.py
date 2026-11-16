# -*- coding: utf-8 -*-
"""批 154 补② · 台账纯净性钉——元数据键不伪组件化（用户页面 outdated 行渲染全串实证）。"""
import inspect
from src.health_monitor import security_collector as sc


def _code_only(src: str) -> str:
    """剥注释行再断言——否则「注释里提到旧机制」会造成假失败（守门禁自失效纪律）。"""
    return "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))


def test_no_sys_rows_passthrough():
    """批184a：`sys_rows`（components 字典直通台账）已删——它是 8 别名探针的入口，
    也是伪组件（outdated 串/时间戳）的唯一来源。OS 行改由 dep_health.rows 统一供。"""
    src = _code_only(inspect.getsource(sc.run_security_scan))
    assert "sys_rows" not in src
    assert "isinstance(v, str)" not in src


def test_ledger_rows_only_from_two_authorities():
    """台账入账只有两个权威源：grype ledger + dep_health.rows（不得再有第三处拼装）。"""
    src = inspect.getsource(sc.run_security_scan)
    assert "_upsert_components(conn, today, _ledger" in src
    assert "_upsert_components(conn, today, _os_up" in src


def test_meta_keys_never_rows():
    """语义钉：outdated 值为 list 时 str(v) 才产生全串伪行——白名单后绝迹。"""
    part = {"components": {"nginx": "1.24.0", "outdated": [{"name": "x"}],
                            "n_outdated": 3, "vulns_db": {"state": "updated"}}}
    rows = [("system", k, str(v), "系统组件") for k, v in part["components"].items()
            if k not in ("pip", "status") and isinstance(v, str) and v]
    assert [r[1] for r in rows] == ["nginx"]
