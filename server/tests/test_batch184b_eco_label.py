"""批184b 守门：D 区生态标签（域+实现）后端供给与三语词条齐全。

前端按 row.source（域）+ row.pkg_mgr（实现）渲染标签；缺任一列就会退化成
「安装途径」模糊文案，同名跨域（PyPI tzdata / 系统 tzdata）在表里再次不可辨。
"""
import inspect
import pathlib
import re

import pytest


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM security_component LIMIT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


@needs_db
def test_ledger_carries_domain_and_impl():
    """台账行必须带 pkg_mgr —— 否则前端 os 域只能显示「系统包」，分不出 dnf/apt。"""
    from src.data_platform.security_store import get_component_ledger
    rows = get_component_ledger()
    if not rows:
        pytest.skip("dev 库台账为空")
    assert "pkg_mgr" in rows[0]
    assert "source" in rows[0]


def test_dep_health_rows_carry_domain():
    """dep_health rows（pip 行与 os 行）都带 source 域，供前端 A/D 区区分。"""
    import src.health_monitor.security_collector as sc
    text = inspect.getsource(sc._collect_components)
    assert '"source": "pip"' in text
    assert '"source": "os"' in text


def test_eco_i18n_keys_in_three_locales():
    """生态词条三语齐全（zh / en / zh-TW）——注册表驱动，缺一即回退原文。"""
    p = pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "locales" / "index.js"
    text = p.read_text(encoding="utf-8")
    for key in ("domain", "ecoPypi", "ecoOs", "ecoOsDnf", "ecoOsApt", "ecoBundle"):
        # `domain` 也会出现在英文句子里（如 urlHint），按行首键定义计；
        # 其余键是专属词条名，全文出现即算（zh/en 同行多键，不能用行首）
        pat = (r'(?m)^\s*"?domain"?\s*:' if key == "domain"
               else rf'\b{key}"?\s*:')
        n = len(re.findall(pat, text))
        assert n == 3, f"{key} 词条 {n} 处（应 3：zh/en/zh-TW）"


def test_ui_renders_eco_column():
    """D 区生态列必须用 source+pkg_mgr（不得回落到 managed_by 单值）。"""
    p = pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "SecurityTab.vue"
    text = p.read_text(encoding="utf-8")
    assert "secpage.domain" in text
    assert "row.pkg_mgr" in text
    assert "prop=\"source\"" in text
