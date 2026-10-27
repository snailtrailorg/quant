# -*- coding: utf-8 -*-
"""批 154 · 安全页双改造——published/outdated 三钉。

用户裁定两语义：①漏洞「首次发现」无风险敞口语义——公开时间才有（DB 字段优先，
AI 摘要对 NULL 项推断标注）；②venv 债务仪表（检查不升级——升级恒走批次）。
"""
from datetime import date

from src.health_monitor.security_collector import _parse_iso_date


class TestPublished:
    def test_parse_iso_date_valid(self):
        assert _parse_iso_date("2024-06-01T00:00:00Z") == date(2024, 6, 1)
        assert _parse_iso_date("2024-06-01") == date(2024, 6, 1)

    def test_parse_iso_date_garbage(self):
        """缺失/畸形 → None（COALESCE 语义：不覆盖已回填真值）。"""
        assert _parse_iso_date(None) is None
        assert _parse_iso_date("") is None
        assert _parse_iso_date("not-a-date") is None

    def test_vulns_finding_carries_published_key(self):
        """解析产物含 published 键（grype vulnerability.published 透传）。"""
        import inspect
        from src.health_monitor import security_collector as sc
        src = inspect.getsource(sc._collect_vulns)
        assert '"published": v.get("published") or None' in src


class TestOutdated:
    def test_strict_newer_only(self):
        """只报 latest 严格新于 current（postgres 18.4 vs 仓库 13.23 实证——发行版仓库
        版本低于实际安装版时不主张滞后；否则「最新版比当前还旧」）。"""
        import inspect
        from src.health_monitor import security_collector as sc
        src = inspect.getsource(sc._collect_components)
        assert "_ver_key(_latest) > _ver_key(_cur_ver)" in src
        assert "_ver_key(latest) != _ver_key(cur_ver)" not in src
    def test_components_collects_outdated(self):
        """components 分区读 dep_audit 缓存（批 154 补②：内联 pip 子进程已删——207s 阻塞根除）。"""
        import inspect
        from src.health_monitor import security_collector as sc
        src = inspect.getsource(sc._collect_components)
        assert "sec:dep_audit" in src          # 读缓存键
        assert 'pip", "list", "--outdated"' not in src   # 内联子进程已绝迹


class TestAiPromptPublished:
    def test_ai_summary_prompts_unknown_published(self):
        """published 缺失项在 AI prompt 附推断指令（DB 真值优先、AI 只兜启发层）。"""
        import inspect
        from src.health_monitor import security_collector as sc
        src = inspect.getsource(sc._ai_summary)
        assert "公开时间未知" in src and "推断大致公开年月" in src
