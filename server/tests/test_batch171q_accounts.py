"""批 171q/171r · 紧急处置账号集 = 交易域账号集（同真源，零兜底）。

不变量在**根本**保证（用户裁定：不给不规范数据擦屁股）：
- DB：live_task.account_id NOT NULL + FK(RESTRICT) + 0174 触发器（mode=live 禁虚拟账户）
- 应用：create_live_task 显式拒绝虚拟账号（ACCOUNT_VIRTUAL_FORBIDDEN）
- 展示：exposure 直读 trading_account(is_virtual=false)，无任何补行/兜底逻辑
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.web_api.routes import risk as R


def _rows(*specs):
    """(id, name, provider, enabled, is_virtual)"""
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


class TestExposureEqualsTradingDomain:

    def test_real_accounts_all_present(self, monkeypatch):
        rows = _rows((1, "中泰XTP测试", "xtp", True, False),
                     (3, "东财 EMT/EMQ", "emt_emq", True, False),
                     (5, "币安开放账号", "binance_perp", True, False),
                     (6, "纸上交易虚拟账户", "paper", False, True))
        out = _run(monkeypatch, rows)
        assert {i["account_id"] for i in out["items"]} == {1, 3, 5}

    def test_virtual_never_shown(self, monkeypatch):
        out = _run(monkeypatch, _rows((9, "纸", "paper", True, True)))
        assert out["items"] == []

    def test_disabled_real_account_still_listed(self, monkeypatch):
        """停用待清仓：仍是真实账号，必须列出（灰态），不能消失。"""
        out = _run(monkeypatch, _rows((8, "停用待清仓", "xtp", False, False)))
        assert [i["account_id"] for i in out["items"]] == [8]
        assert out["items"][0]["enabled"] is False

    def test_no_synthetic_rows(self, monkeypatch):
        """运行中但账号表无行 ⇒ **不补行**（该场景已被 FK/触发器从根本禁止；
        若出现即数据层不变量破损，应在下方真库守门里红，而不是页面兜底）。"""
        out = _run(monkeypatch, _rows((1, "real", "xtp", True, False)), running=(1, 77))
        assert {i["account_id"] for i in out["items"]} == {1}


class TestCreatePathRejectsVirtual:
    def test_live_task_on_virtual_account_400(self):
        """应用层闸：live 任务绑定虚拟账户 ⇒ 400 ACCOUNT_VIRTUAL_FORBIDDEN。"""
        from src.web_api.errors import ApiError
        from src.web_api.routes import trading as T

        strat = ("1", "s", "py", "600000.SHSE", "xtp", True, "[]", "{}", "{}", "{}", True)
        acct = (502, True)      # 虚拟账户

        def _cur(val):
            c = MagicMock()
            c.fetchone.return_value = val
            return c

        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False
        conn.execute.side_effect = [_cur(strat), _cur(acct)]

        body = {"name": "t", "strategy_id": "1", "symbol": "600000.SHSE",
                "account_id": 502, "initial_capital": 100000, "mode": "live"}
        with patch.object(T, "get_conn", MagicMock(return_value=conn)),              patch("src.web_api.auth.load_effective_permissions",
                   return_value=({"live_control"}, {})),              patch.object(T, "audit_log"):
            with pytest.raises(ApiError) as ei:
                T.create_live_task(body, {"username": "u", "role": "admin"})
        assert ei.value.code == "ACCOUNT_VIRTUAL_FORBIDDEN"


class _DbUp:
    @staticmethod
    def up():
        try:
            from src.data_platform.db import get_conn
            with get_conn() as c:
                c.execute("SELECT 1 FROM live_task LIMIT 1")
            return True
        except Exception:
            return False


needs_db = pytest.mark.skipif(not _DbUp.up(), reason="真库（无 dev 库自动跳过）")


class TestRootInvariantRealDb:
    @needs_db
    def test_no_live_task_on_virtual_account(self):
        """数据层不变量体检：现存数据里不存在「实盘任务×虚拟账户」。"""
        from src.data_platform.db import get_conn
        with get_conn() as c:
            bad = c.execute("""
                SELECT lt.id FROM live_task lt JOIN trading_account ta ON ta.id = lt.account_id
                WHERE lt.mode = 'live' AND ta.is_virtual
            """).fetchall()
        assert bad == [], f"实盘任务绑定虚拟账户（数据破损）: {bad}"

    @needs_db
    def test_trigger_blocks_virtual_binding(self):
        """触发器实证：直接写库也被拒（任何写入路径）。"""
        import psycopg
        from src.data_platform.db import get_conn
        with get_conn() as c:
            vid = c.execute("SELECT id FROM trading_account WHERE is_virtual LIMIT 1").fetchone()[0]
            with pytest.raises(psycopg.errors.RaiseException):
                c.execute(
                    "INSERT INTO live_task (name,strategy_id,symbol,params,strategy_snapshot,"
                    "status,account_id,mode) VALUES ('x','1','600000.SHSE','{}','{}','stopped',%s,'live')",
                    (vid,))
            c.rollback()
