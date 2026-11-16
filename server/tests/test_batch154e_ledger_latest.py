# -*- coding: utf-8 -*-
"""批 154 补⑤ · 台账 latest 三态（服务端单源）。

用户裁定：已是最新也要显示（不能空），否则看不出是否查过。
  - 命中 outdated → 真实新版本（前端橙黄）
  - pip 域 / 已知 rpm 键未命中 → 当前版本（=已最新，前端灰色）
  - 其余（无发行版坐标）→ None（前端 —，不假声称）
"""
import inspect
from src.web_api.routes import security as sec


class TestLedgerLatest:
    def test_note_attached_in_route(self):
        """批 155 随形：snapshot 输出 dep_health 汇总+组件行新列（台账直读零现算）。"""
        src = inspect.getsource(sec.security_snapshot)
        assert "_dep_health_summary" in src and "has_update" in src
        assert "stale_days" in src and "latest_version" in src

    def test_os_latest_comes_from_judged_only(self):
        """批184b：os 域 latest 只认 judged（8 别名常量随 184a 删除）——
        不得用 version 冒充「已最新」，否则 unknown 的包会被显示成无更新。"""
        src = inspect.getsource(sec.security_snapshot)
        code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        assert 'elif _r["source"] == "pip"' in code
        assert "_RPM_KNOWN" not in code

    def test_outdated_error_keyword_purged(self):
        """批184a：脏行源头（sys_rows 直通）已删——元键不可能再入台账，无需运行期自愈清单。"""
        from src.health_monitor import security_collector as sc
        src = "\n".join(ln for ln in inspect.getsource(sc.run_security_scan).splitlines()
                         if not ln.strip().startswith("#"))
        assert "sys_rows" not in src
