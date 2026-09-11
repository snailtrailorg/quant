"""批15 市场操作权限测试（market_op 维）。

覆盖：
- market_op_allowed 解析：user 行覆盖 role 行 / deny 优先（同层双行）/ 无行 False /
  读库失败 False / role allow 生效
- check_order 2.5 卡口：denied 市场 BUY 拒 + risk_log 落行 / SELL 平仓放行（F-31 同哲学）/
  operator 空=拒 critical（旧 --id 路径预期）/ _role_of 无命中（软删用户）=拒 /
  检查异常=deny critical（拒绝可见）
- operator 注入：strategy.place_order 读实例属性，order["operator"] 服务端钉死
"""
from unittest.mock import MagicMock, patch

import pytest

from src.risk_control.risk import RiskControl, RiskState


# ——— market_op_allowed 解析 ———


def _perm_conn(rows):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn.execute.return_value = cur
    return conn


class TestMarketOpAllowed:
    def test_no_rows_denied(self):
        """无行=False（组未配置即拒——fail-closed 缺省；新组零行=天然零权限）。"""
        with patch("src.data_platform.db.get_conn", return_value=_perm_conn([])):
            from src.data_platform.perms import market_op_allowed
            assert market_op_allowed("u1", "viewer", "astock") is False

    def test_role_allow(self):
        rows = [("role", "trader", "allow")]
        with patch("src.data_platform.db.get_conn", return_value=_perm_conn(rows)):
            from src.data_platform.perms import market_op_allowed
            assert market_op_allowed("u1", "trader", "astock") is True

    def test_user_allow_over_role_absence(self):
        """user 行 allow 覆盖 role 层无行（显式放行）。"""
        rows = [("user", "u1", "allow")]
        with patch("src.data_platform.db.get_conn", return_value=_perm_conn(rows)):
            from src.data_platform.perms import market_op_allowed
            assert market_op_allowed("u1", "viewer", "etf") is True

    def test_user_deny_beats_role_allow(self):
        rows = [("role", "trader", "allow"), ("user", "u1", "deny")]
        with patch("src.data_platform.db.get_conn", return_value=_perm_conn(rows)):
            from src.data_platform.perms import market_op_allowed
            assert market_op_allowed("u1", "trader", "etf") is False

    def test_same_layer_deny_priority(self):
        """同层 allow+deny 双行并存（手工 SQL 可造）→ deny 优先。"""
        rows = [("role", "trader", "allow"), ("role", "trader", "deny")]
        with patch("src.data_platform.db.get_conn", return_value=_perm_conn(rows)):
            from src.data_platform.perms import market_op_allowed
            assert market_op_allowed("u1", "trader", "etf") is False

    def test_db_failure_denied(self):
        """读库失败=False（fail-closed，对齐链上件）。"""
        class BoomConn:
            def __enter__(self):
                raise RuntimeError("PG 挂了")

            def __exit__(self, *a):
                return False

        with patch("src.data_platform.db.get_conn", return_value=BoomConn()):
            from src.data_platform.perms import market_op_allowed
            assert market_op_allowed("u1", "trader", "etf") is False


# ——— check_order 2.5 卡口 ———


def _rc():
    c = RiskControl()
    c.is_halted = lambda: False
    c.is_live_trading_allowed = lambda market: True
    c._get_global_state = lambda a: RiskState(halted=False, total_drawdown=0.0, daily_loss=0.0)
    return c


BUY = {"symbol": "600000.SHSE", "action": "BUY", "volume": 100, "price": 10.0, "operator": "u1"}
SELL = {"symbol": "600000.SHSE", "action": "SELL", "volume": 100, "price": 10.0, "operator": "u1"}


class TestCheckOrderMarketOp:
    def test_denied_buy_rejected_and_logged(self):
        """denied 市场 BUY 拒单 + risk_log 落行（check_order 出口统一）。"""
        c = _rc()
        c._role_of = staticmethod(lambda u: "trader")
        ins_conn = MagicMock()
        ins_conn.__enter__.return_value = ins_conn
        with patch("src.data_platform.perms.market_op_allowed", return_value=False), \
             patch("src.risk_control.risk.get_conn", return_value=ins_conn):
            d = c.check_order(dict(BUY))
        assert not d.approved and "市场操作权限拒绝" in d.reason
        # risk_log INSERT 被调用（审计面）
        sql = ins_conn.execute.call_args[0][0]
        assert "INSERT INTO risk_log" in sql

    def test_sell_passthrough(self):
        """SELL/平仓放行（F-31 同哲学：准入拦开仓，平仓=减风险方向）。"""
        c = _rc()
        c._role_of = staticmethod(lambda u: "trader")
        with patch("src.data_platform.perms.market_op_allowed", return_value=False) as m:
            d = c.check_order(dict(SELL))
        assert d.approved, f"SELL 应放行: {d.reason}"
        assert not m.called   # SELL 不触发 market_op 查询

    def test_missing_operator_rejected_critical(self):
        """operator 空=BUY 拒 critical（旧 --id 路径/异常构造 fail-closed）。"""
        c = _rc()
        ins_conn = MagicMock()
        ins_conn.__enter__.return_value = ins_conn
        o = dict(BUY)
        del o["operator"]
        with patch("src.risk_control.risk.get_conn", return_value=ins_conn):
            d = c.check_order(o)
        assert not d.approved and d.severity == "critical" and "operator" in d.reason

    def test_missing_operator_sell_allowed(self):
        """SELL 无 operator 同样放行（代码盲审 B-P2：F-31 不能把人锁在市场里——
        旧 --id 路径持仓任务的止损不能被 operator 缺失卡死）。"""
        c = _rc()
        o = dict(SELL)
        del o["operator"]
        d = c.check_order(o)
        assert d.approved, f"无 operator 的 SELL 应放行: {d.reason}"

    def test_role_of_no_hit_denied(self):
        """软删/禁用用户 _role_of 无命中 → 按拒（fail-closed）。"""
        c = _rc()
        c._role_of = staticmethod(lambda u: None)
        ins_conn = MagicMock()
        ins_conn.__enter__.return_value = ins_conn
        with patch("src.risk_control.risk.get_conn", return_value=ins_conn):
            d = c.check_order(dict(BUY))
        assert not d.approved and "市场操作权限拒绝" in d.reason

    def test_check_exception_denied_critical(self):
        """2.5 整段异常 → deny critical（拒绝可见，不裸抛绕过审计）。"""
        c = _rc()

        def boom(u):
            raise RuntimeError("users 表读失败")

        c._role_of = staticmethod(boom)
        ins_conn = MagicMock()
        ins_conn.__enter__.return_value = ins_conn
        with patch("src.risk_control.risk.get_conn", return_value=ins_conn):
            d = c.check_order(dict(BUY))
        assert not d.approved and d.severity == "critical"
        assert "市场操作权限检查异常" in d.reason


# ——— operator 注入（服务端钉死）———


class TestOperatorInjection:
    def test_place_order_reads_instance_attr(self, monkeypatch):
        """place_order 构造的 order['operator']=实例属性（runner 注入位）。"""
        from src.strategy_framework.strategy import Strategy, StrategyConfig, Signal, Action

        cfg = StrategyConfig(id="t15", name="t", type="astock_analysis",
                             symbol="600000.SHSE", adapter="xtp")
        s = Strategy(cfg, None)
        s.operator = "bernard"

        captured = {}

        class FakeRC:
            def check_order(self, order, account=""):
                captured.update(order)
                from src.risk_control.risk import RiskDecision
                return RiskDecision(approved=False, reason="拒（仅捕获 order）")

        with patch("src.risk_control.risk.RiskControl.get", return_value=FakeRC()):
            s.place_order(Signal(action=Action.BUY, score=0.5, symbol="600000.SHSE",
                                 volume=100, price=10.0, reason="t"))
        assert captured.get("operator") == "bernard"

    def test_signal_cannot_smuggle_operator(self, monkeypatch):
        """Signal 自带 operator 键进不了 order（服务端钉死——构造点只读实例属性）。"""
        from src.strategy_framework.strategy import Strategy, StrategyConfig, Signal, Action

        cfg = StrategyConfig(id="t15b", name="t", type="astock_analysis",
                             symbol="600000.SHSE", adapter="xtp")
        s = Strategy(cfg, None)   # 不设 operator → ""

        captured = {}

        class FakeRC:
            def check_order(self, order, account=""):
                captured.update(order)
                from src.risk_control.risk import RiskDecision
                return RiskDecision(approved=False, reason="拒（仅捕获 order）")

        sig = Signal(action=Action.BUY, score=0.5, symbol="600000.SHSE",
                     volume=100, price=10.0, reason="t")
        sig.operator = "attacker"   # 恶意自报
        with patch("src.risk_control.risk.RiskControl.get", return_value=FakeRC()):
            s.place_order(sig)
        assert captured.get("operator") == ""


# ——— market_op 管理端点（update_permissions market_op 维）———


class TestMarketOpEndpoints:
    def _call_save(self, role, res_map, dimension="market_op"):
        from src.web_api.routes.auth_routes import update_permissions
        return update_permissions(role, {"resources": res_map}, dimension=dimension,
                                  payload={"username": "admin"})

    def test_data_dimension_retired(self):
        """data 维已退役 → BAD_DIMENSION 400（批15：脱敏+markets 存而不灵全下线）。"""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            self._call_save("trader", {}, dimension="data")
        assert ei.value.status_code == 400

    def test_market_op_save_roundtrip(self):
        """五键全量重写（勾=allow/不勾=deny）→ DELETE+INSERT 落库调用序+参数精确比对
        （代码盲审 A-P2：只断言 SQL 片段不防参数错位/effect 值错）。"""
        conn = MagicMock()
        conn.__enter__.return_value = conn
        with patch("src.data_platform.db.get_conn", return_value=conn):
            out = self._call_save("trader", {"astock": "allow", "etf": "deny"})
        assert out["resources"] == {"astock": "allow", "etf": "deny"}
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("DELETE FROM permission" in s and "dimension=%s" in s for s in sqls)
        inserts = [c for c in conn.execute.call_args_list if "INSERT INTO permission" in c.args[0]]
        # INSERT 参数列序：(role, dimension, resource, effect, updated_by)——取 resource/effect/by
        got = {(c.args[1][2], c.args[1][3], c.args[1][4]) for c in inserts}
        assert got == {("astock", "allow", "admin"), ("etf", "deny", "admin")}

    def test_market_op_bad_resource_rejected(self):
        """未知市场键（如旧 data 维的 'crypto' 合并键）→ 400 BAD_RESOURCE。"""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            self._call_save("trader", {"crypto": "allow"})   # 批15：binance/okx 拆两键，crypto 不在键集
        assert ei.value.status_code == 400 and ei.value.detail == "BAD_RESOURCE"

    def test_get_permissions_has_market_op_segment(self):
        """GET /api/permissions 响应含 market_op 段（keys 单源五键）且无 data 段。"""
        from src.web_api.routes.auth_routes import get_permissions

        def side_conn(*a, **k):
            c = MagicMock()
            c.__enter__.return_value = c
            c.execute.return_value.fetchall.return_value = []
            return c

        with patch("src.data_platform.db.get_conn", side_effect=side_conn):
            r = get_permissions(payload={"username": "admin"})
        assert list(r["market_op"]["keys"]) == ["convertible", "etf", "astock", "binance_perp", "okx_perp"]
        assert "data" not in r

