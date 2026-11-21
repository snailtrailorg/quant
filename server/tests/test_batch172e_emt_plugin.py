"""批172e P4：EMT 插件载体守门（注册/清单/号段/import 干净）。"""
from __future__ import annotations

import subprocess
import sys


def test_emt_registered_with_execution_cap():
    import src.providers as p
    p.ensure_registered()
    from src.registry import DOMAIN_TRADING, PORT_CAPS, REGISTRY
    plugin = REGISTRY[(DOMAIN_TRADING, "emt_emq")]
    caps = set(plugin.manifest.port_caps)
    assert "execution" in caps and caps <= PORT_CAPS


def test_manifest_terminal_domains_no_overlap_with_xtp():
    from src.providers.emt.manifest import Manifest
    from src.providers.xtp.manifest import Manifest as XManifest
    m, x = Manifest(), XManifest()
    assert m.domain == x.domain == "trading_account"
    assert m.terminal_domains["td"] == (20, 119)
    assert x.terminal_domains["td"] == (2, 99)   # 与 XTP 2..99 不交叠


def test_import_providers_does_not_pull_vnpy_or_emt_sdk():
    code = ("import src.providers, sys; "
            "print('vnpy' in sys.modules, 'emt_trader_api' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False False", out.stdout
