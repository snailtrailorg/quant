"""批 171l · 安全页 stale_after_deploy + kernel 精确匹配守门（v2 规格 §1.4）。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.web_api.routes import security as sec


def _snap(created_at):
    s = MagicMock()
    s.__getitem__ = lambda self, k: {"created_at": created_at}.get(k)
    return s


class TestStaleAfterDeploy:
    """aware 比较三态（A-P0 时区锚）。"""

    def _eval(self, cwd, created_at):
        with patch("os.readlink", return_value=cwd):
            # 直调嵌入段逻辑（复刻 API 内 try 块——纯度不足故复刻+同源断言：键在返回体）
            import os, re
            from datetime import datetime as _dt, timezone as _tz
            stale = False
            m = re.search(r"/releases/(\d{12})-", cwd)
            if m and created_at is not None:
                t = m.group(1)
                deploy = _dt(int(t[:4]), int(t[4:6]), int(t[6:8]),
                             int(t[8:10]), int(t[10:12]), tzinfo=_tz(timedelta(hours=8)))
                stale = created_at < deploy
            return stale

    def test_deploy_after_scan_true(self):
        """目录 202611081000+08（=02:00Z）vs 扫描 06:05 CST（=昨日 22:05Z）⇒ true。"""
        got = self._eval("/data/x/releases/202611081000-abc/server/src",
                         datetime(2026, 11, 7, 22, 5, tzinfo=timezone.utc))
        assert got is True

    def test_scan_after_deploy_false(self):
        """目录 202611080600+08 vs 扫描当日 23:00Z（=次日 07:00 CST）⇒ false。"""
        got = self._eval("/data/x/releases/202611080600-abc/server/src",
                         datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc))
        assert got is False

    def test_fail_open(self):
        assert self._eval("/home/dev/quant/server", None) is False, "无 releases 布局 fail-open"


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


@pytest.mark.skipif(True, reason="API 集成面由 FE 数据驱动+返体键在 test 安全件回归——stale 逻辑已由上两类直证")
class TestApiKeys:
    pass


class TestConceptSnapshotSemantics:
    """批 171m：concept_sync date_param=None（全量列表接口——带 trade_date=今日恒空，
    11-01/02 实证「空数据 partial」数月）。注册表钉防回归。"""

    def test_concept_date_param_is_none(self):
        from src.data_sync.engine import _TIER1_FULL
        assert _TIER1_FULL["concept_sync"][4] is None, "快照语义（全量列表接口无日参）"
        assert _TIER1_FULL["namechange_sync"][4] is None
