"""批 171q · 紧急处置账号集一致性守门（漏账号=灾难，本文件钉死三条不变量）。

I1 完备：trading_account(is_virtual=false) 每一行必须出现在 exposure（一个都不能少）
I2 纯净：is_virtual=true（纸上交易）不得出现在 exposure
I3 兜底：任何 running 实盘任务的 account_id 必须在 exposure（账号表缺行也要补入，
        标 unregistered）——紧急场景宁可多一行不可漏一个
"""
from unittest.mock import MagicMock, patch

import pytest

from src.web_api.routes import risk as R


def _rows(*specs):
    """"(id, name, provider, enabled, is_virtual)"""
    return [tuple(s) for s in specs]


def _run(monkeypatch, rows, running=()):
    monkeypatch.setattr("src.data_platform.config_store.trading_accounts_with_flags",
                        lambda: rows)
    monkeypatch.setattr("src.data_platform.config_store.exposure_snapshots", lambda: {})
    monkeypatch.setattr("src.data_platform.config_store.live_running_tasks",
                        lambda: [(f"t{i}", a) for i, a in enumerate(running)])
    r = MagicMock()
    r.hgetall.return_value = {"ts": "9999999999"}
    with patch("src.quant_common.redis_client.business_redis", return_value=r), \
         patch("src.risk_control.risk.RiskControl.get") as rc:
        rc.return_value.halt_ts.return_value = None
        return R.risk_exposure(_p={"username": "t"})


class TestAccountSetInvariants:

    def test_i1_no_real_account_missing(self, monkeypatch):
        rows = _rows((1, "中泰XTP测试", "xtp", True, False),
                     (3, "东财 EMT/EMQ", "emt_emq", True, False),
                     (5, "币安开放账号", "binance_perp", True, False),
                     (6, "纸上交易虚拟账户", "paper", False, True))
        out = _run(monkeypatch, rows)
        ids = {i["account_id"] for i in out["items"]}
        assert ids == {1, 3, 5}, "每个真实账号都必须在（一个都不能漏）"

    def test_i2_virtual_excluded(self, monkeypatch):
        rows = _rows((1, "real", "xtp", True, False),
                     (9, "纸上交易虚拟账户", "paper", True, True))
        out = _run(monkeypatch, rows)
        assert 9 not in {i["account_id"] for i in out["items"]}, "虚拟账户是噪音，不得出现"

    def test_i3_running_orphan_account_included(self, monkeypatch):
        """账号表缺行但任务在跑 ⇒ 必须补入并标 unregistered（漏=损失）。"""
        rows = _rows((1, "real", "xtp", True, False))
        out = _run(monkeypatch, rows, running=(1, 77))
        item = next(i for i in out["items"] if i["account_id"] == 77)
        assert item["unregistered"] is True and item["enabled"] is True
        assert "未登记" in item["name"]

    def test_i3_disabled_real_account_still_listed(self, monkeypatch):
        """未启用但真实（如刚停用待清仓）——仍须列出（灰态），不可因 enabled=false 消失。"""
        rows = _rows((1, "real", "xtp", True, False),
                     (8, "停用待清仓", "xtp", False, False))
        out = _run(monkeypatch, rows)
        assert 8 in {i["account_id"] for i in out["items"]}
