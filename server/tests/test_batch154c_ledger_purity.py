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


def test_vendored_artifacts_excluded_from_ledger():
    """setuptools/pip 内置副本不是独立安装包：不得入台账（会覆盖真装版本）。"""
    arts = [
        {"type": "python", "name": "platformdirs", "version": "4.14.5",
         "locations": [{"path": "/platformdirs-4.14.5.dist-info/METADATA"}]},
        {"type": "python", "name": "platformdirs", "version": "4.4.0",
         "locations": [{"path": "/setuptools/_vendor/platformdirs-4.4.0.dist-info/METADATA"}]},
        {"type": "binary", "name": "Simple Launcher", "version": "1.1.0",
         "locations": [{"path": "/pip/_vendor/distlib/t64.exe"}]},
    ]
    rows = sc._ledger_from_sbom(arts, None)
    assert [(r[1], r[2]) for r in rows] == [("platformdirs", "4.14.5")]


def test_freeze_wins_over_sbom_for_pip_domain():
    """pip 域版本真源=freeze；SBOM 同名 pip 行不得覆盖，os/binary 行照收。"""
    rows = sc._merge_ledger(
        {"platformdirs": "4.14.5"},
        [("pip", "platformdirs", "4.4.0", "pip 安装"),
         ("os", "expat", "2.5.0-6.al8", "OS rpm")])
    by = {(r[0], r[1]): r[2] for r in rows}
    assert by[("pip", "platformdirs")] == "4.14.5"
    assert by[("os", "expat")] == "2.5.0-6.al8"


def test_meta_keys_never_rows():
    """语义钉：outdated 值为 list 时 str(v) 才产生全串伪行——白名单后绝迹。"""
    part = {"components": {"nginx": "1.24.0", "outdated": [{"name": "x"}],
                            "n_outdated": 3, "vulns_db": {"state": "updated"}}}
    rows = [("system", k, str(v), "系统组件") for k, v in part["components"].items()
            if k not in ("pip", "status") and isinstance(v, str) and v]
    assert [r[1] for r in rows] == ["nginx"]
