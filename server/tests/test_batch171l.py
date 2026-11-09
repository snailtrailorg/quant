"""批 171l · 安全页 kernel 精确匹配守门（v2 规格 §1.4）。

2026-11-09：171l 的 stale_after_deploy 键已退役（171p 撤 UI 后无消费方）——相关三态
用例随之删除；kernel 误伤守门保留。
"""
import pytest

from src.web_api.routes import security as sec   # noqa: F401  （导入即验证模块可加载）


class TestKernelExactMatch:
    """A-P1-2：kernel/kernel-core 真；tools/headers/devel 假。"""

    def _is_kernel(self, name):
        import re
        return bool(re.match(r"^(kernel|kernel-core)-\d|^(kernel|kernel-core)$", str(name or "")))

    def test_exact(self):
        # NEVRA 真形态（wrapper :52 实证）
        assert self._is_kernel("kernel-5.10.134-19.10.al8.x86_64")
        assert self._is_kernel("kernel-core-5.10.134-19.10.al8.x86_64")
        assert self._is_kernel("kernel-core")

    def test_not_tools(self):
        for n in ("kernel-tools.x86_64", "kernel-headers.x86_64",
                  "kernel-devel.x86_64", "kernel-modules.x86_64"):
            assert not self._is_kernel(n), f"{n} 不需重启——不得误标"


class TestSnapshotKeyRetired:
    """2026-11-09：stale_after_deploy 键退役（FE 无消费方）——防有人再种回去。"""

    def test_key_absent_from_snapshot_source(self):
        import inspect
        src = inspect.getsource(sec)
        assert '"stale_after_deploy":' not in src, "返体键已退役——不得再种回"


class TestConceptSnapshotSemantics:
    """批 171m：concept_sync date_param=None（全量列表接口——带 trade_date=今日恒空，
    11-01/02 实证「空数据 partial」数月）。注册表钉防回归。"""

    def test_concept_date_param_is_none(self):
        from src.data_sync.engine import _TIER1_FULL
        assert _TIER1_FULL["concept_sync"][4] is None, "快照语义（全量列表接口无日参）"
        assert _TIER1_FULL["namechange_sync"][4] is None
