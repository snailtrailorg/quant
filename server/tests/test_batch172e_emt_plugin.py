"""批172e P4：EMT 插件载体守门（注册/清单/号段/import 干净）。"""
from __future__ import annotations

import subprocess
import sys


def test_emt_trade_entity_has_no_ports_until_p4c():
    """EMT 交易实体已注册但端口为空集（P4c 才加 execution）——诚实声明。"""
    import src.providers as p
    p.ensure_registered()
    from src.registry import DOMAIN_TRADING, PORT_CAPS, REGISTRY
    plugin = REGISTRY[(DOMAIN_TRADING, "emt_emq")]
    caps = set(plugin.manifest.port_caps)
    assert caps == set() and caps <= PORT_CAPS


def test_emt_market_entity_carried_by_bridge():
    """EMT 行情是另一个实体（market_interface），载体来自 md_gateway 注册表。"""
    import src.providers as p
    p.ensure_registered()
    from src.registry import DOMAIN_MARKET, REGISTRY
    plugin = REGISTRY[(DOMAIN_MARKET, "emt_emq")]
    assert plugin.manifest.port_caps == {"market_data"}


def test_manifest_terminal_domains_declared():
    """号段声明：EMT broker 侧 20..119、XTP 2..99；互斥由会话槽 PK 的 provider 维度保证。"""
    from src.providers.emt.manifest import Manifest
    from src.providers.xtp.manifest import Manifest as XManifest
    m, x = Manifest(), XManifest()
    assert m.domain == x.domain == "trade_interface"
    assert m.terminal_domains["td"] == (20, 119)   # EMT SDK 终端号段
    assert x.terminal_domains["td"] == (2, 99)


def test_import_providers_does_not_pull_vnpy_or_emt_sdk():
    code = ("import src.providers, sys; "
            "print('vnpy' in sys.modules, 'emt_trader_api' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False False", out.stdout
