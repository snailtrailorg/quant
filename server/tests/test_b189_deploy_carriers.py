"""批189：部署载体集与 registry 载体集等价（I4：进程 ⇔ 行存在 ∧ provider 声明该角色载体）。

守门三件：
1. `deploy_td_exec_providers` == registry 交易域「有 execution 端口」的 provider（P4c 前只有 xtp）；
2. 各 inventory 的 `deploy_hub_md_providers` == registry 行情域已登记 provider；
3. wrapper 的选择 SQL 不得以 `enabled`/`impl` 当就绪条件（那会把「建进程」偷换成「配置开关」）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
DEPLOY = ROOT / "deploy"


def _yaml_list(path: Path, key: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"^{key}\s*:\s*\[([^\]]*)\]", text, re.M)
    assert m, f"{path} 缺 {key}"
    return [x.strip() for x in m.group(1).split(",") if x.strip()]


def _registry_sets() -> tuple[set[str], set[str]]:
    import src.providers as p
    p.ensure_registered()
    from src.registry import DOMAIN_MARKET, DOMAIN_TRADING, registered
    market = {prov for (dom, prov) in registered() if dom == DOMAIN_MARKET}
    trade = {prov for (dom, prov) in registered() if dom == DOMAIN_TRADING}
    from src.registry import UnsupportedCapability, pick
    exec_capable = set()
    for prov in trade:
        try:
            pick(DOMAIN_TRADING, prov, {"execution"}, "plugin")
            exec_capable.add(prov)
        except UnsupportedCapability:
            pass
    return market, exec_capable


def test_td_exec_provider_list_matches_carriers():
    _, exec_capable = _registry_sets()
    listed = set(_yaml_list(DEPLOY / "inventory/group_vars/all.yml", "deploy_td_exec_providers"))
    assert listed == exec_capable, f"group_vars {sorted(listed)} ≠ registry {sorted(exec_capable)}"


def test_hub_provider_lists_match_carriers():
    market, _ = _registry_sets()
    for name in ("quant-prod.yml", "quant-staging.yml", "sandbox.yml"):
        listed = set(_yaml_list(DEPLOY / "inventory/group_vars" / name, "deploy_hub_md_providers"))
        assert listed == market, f"{name}: {sorted(listed)} ≠ {sorted(market)}"


def test_hbcheck_expected_providers_match_group_vars():
    """hbcheck 独立装位，白名单字面量必须与 group_vars 等价（否则期望集分裂=假告警）。"""
    listed = set(_yaml_list(DEPLOY / "inventory/group_vars/quant-prod.yml", "deploy_hub_md_providers"))
    text = (DEPLOY / "wrappers/quant-hbcheck").read_text(encoding="utf-8")
    m = re.search(r'HB_MD_PROVIDERS="([^"]+)"', text)
    assert m, "hbcheck 缺行情 provider 白名单"
    embedded = {x.strip().strip("'") for x in m.group(1).split(",")}
    assert embedded == listed, f"hbcheck {sorted(embedded)} ≠ group_vars {sorted(listed)}"


def test_wrapper_does_not_gate_units_on_enabled_or_impl():
    """I4：enabled 只管干活/空转；impl 只是形态标记——都不得进入单元选择条件。"""
    text = (DEPLOY / "wrappers/quant-dbro").read_text(encoding="utf-8")
    sqls = [ln for ln in text.splitlines()
            if ("FROM market_interface" in ln or "FROM trade_interface" in ln) and "SELECT" in ln]
    assert sqls, "wrapper 里找不到选表 SQL"
    for ln in sqls:
        assert "enabled" not in ln, ln
        assert "impl" not in ln, ln
