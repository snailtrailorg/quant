"""SB 风控批次测试（SB1/SB2/SB3，2026-08-17 稳定性检查 F-29/F-31/F-30/F-23/F-42/F-43）。

覆盖：
- F-29 fail-closed：快照数据源故障/无数据 → 拒单（不再"故障时限制归零"）
- F-31 日亏限额：BUY 拒、SELL 放行（能止损）
- F-30 规则完整性：部分规则不 KeyError
- F-23 规则热加载：60s TTL 后 Web 修改生效
- F-42 ETF 前缀：588 科创 ETF 归 etf 分项
- F-43/F-28 风控层兜底：0 价/0 量单拒绝
"""
import pytest

from src.risk_control.risk import RiskControl, RiskState, DEFAULT_RULES


@pytest.fixture
def rc():
    """独立实例（不污染单例）。is_halted/开关均打桩，聚焦被测逻辑。"""
    saved = RiskControl._instance
    c = RiskControl()
    RiskControl._instance = saved  # 不注册为单例
    c.is_halted = lambda: False
    c.is_live_trading_allowed = lambda market: True
    return c


def _state(drawdown=0.0, daily=0.0, available=True):
    return RiskState(halted=False, total_drawdown=drawdown, daily_loss=daily, available=available)


BUY = {"symbol": "600000.SHSE", "action": "BUY", "volume": 100, "price": 10.0}
SELL = {"symbol": "600000.SHSE", "action": "SELL", "volume": 100, "price": 10.0}


class TestFailClosed:
    def test_pg_down_rejects(self, rc, monkeypatch):
        """F-29：PG 异常 → available=False → 拒单。"""
        def boom(account):
            return _state(available=False)
        monkeypatch.setattr(rc, "_get_global_state", boom)
        d = rc.check_order(dict(BUY))
        assert not d.approved and d.severity == "critical"
        assert "fail-closed" in d.reason or "不可用" in d.reason

    def test_get_global_state_exception_marks_unavailable(self, rc, monkeypatch):
        """真实路径：get_conn 抛异常 → available=False（而非全零放行）。"""
        import src.risk_control.risk as risk_mod

        class BoomConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                raise RuntimeError("PG 挂了")

        monkeypatch.setattr(risk_mod, "get_conn", lambda: BoomConn())
        st = rc._get_global_state("")
        assert st.available is False

    def test_no_snapshot_marks_unavailable(self, rc, monkeypatch):
        """无任何快照 → available=False（新系统首单也应等待风控数据）。"""
        import src.risk_control.risk as risk_mod

        class EmptyConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                class Cur:
                    def fetchone(self):
                        return None
                return Cur()

        monkeypatch.setattr(risk_mod, "get_conn", lambda: EmptyConn())
        st = rc._get_global_state("")
        assert st.available is False

    def test_valkey_down_halt_check_conservative(self, rc, monkeypatch):
        """Valkey 挂 → is_halted 抛 → 按熔断保守拒单。"""
        def redis_boom():
            raise RuntimeError("valkey 挂")
        rc.is_halted = redis_boom
        d = rc.check_order(dict(BUY))
        assert not d.approved and d.severity == "critical"


class TestDailyLossSellPassthrough:
    def test_buy_rejected(self, rc, monkeypatch):
        monkeypatch.setattr(rc, "_get_global_state", lambda a: _state(daily=0.10))
        d = rc.check_order(dict(BUY))
        assert not d.approved and "禁止开仓" in d.reason

    def test_sell_allowed(self, rc, monkeypatch):
        """F-31 核心：日亏超限时 SELL 必须能过全局门（去到分市场检查）。"""
        monkeypatch.setattr(rc, "_get_global_state", lambda a: _state(daily=0.10))
        d = rc.check_order(dict(SELL))
        assert d.approved, f"SELL 应放行，被拒: {d.reason}"


class TestRulesIntegrity:
    def test_partial_rules_no_keyerror(self, rc):
        """F-30：只配 global 规则，etf_conv 路径不 KeyError。"""
        merged = rc._merged_rules({"global": {"max_drawdown": 0.2}})
        assert set(merged.keys()) == set(DEFAULT_RULES.keys())
        rc._rules = merged
        rc._get_global_state = lambda a: _state()
        d = rc.check_order(dict(BUY))  # 走 _check_etf_conv
        assert d.approved

    def test_hot_reload(self, rc, monkeypatch):
        """F-23：TTL 过期后重读规则。"""
        import time as _t
        rc._rules = rc._merged_rules(None)
        assert rc._rules["global"]["max_drawdown"] == 0.15
        rc._rules_loaded_at = _t.time() - 61  # 强制过期
        monkeypatch.setattr(rc, "_load_rules_from_db", lambda: {"global": {"max_drawdown": 0.05}})
        rc._maybe_reload_rules()
        assert rc._rules["global"]["max_drawdown"] == 0.05

    def test_reload_failure_keeps_old(self, rc, monkeypatch):
        rc._rules_loaded_at = 0  # 过期
        monkeypatch.setattr(rc, "_load_rules_from_db", lambda: (_ for _ in ()).throw(RuntimeError("PG 挂")))
        rc._maybe_reload_rules()  # 不应抛
        assert rc._rules["global"]["max_drawdown"] == 0.15


class TestMarketOf:
    def test_etf_588(self, rc):
        assert rc._market_of("588000.SHSE") == "etf"  # F-42

    def test_others_unchanged(self, rc):
        assert rc._market_of("600000.SHSE") == "astock"
        assert rc._market_of("113000.SHSE") == "convertible"
        assert rc._market_of("510300.SHSE") == "etf"
        assert rc._market_of("159915.SZSE") == "etf"
        assert rc._market_of("BTCUSDT.BINANCE") == "binance_perp"


class TestOrderValidity:
    def test_zero_price_rejected(self, rc):
        """F-43：price=0 不再绕过金额上限。"""
        d = rc._check_etf_conv({"volume": 100, "price": 0})
        assert not d.approved and d.severity == "critical"

    def test_zero_volume_rejected(self, rc):
        """F-28 风控层兜底：volume=0 废单拒绝。"""
        d = rc._check_etf_conv({"volume": 0, "price": 10.0})
        assert not d.approved and d.severity == "critical"

    def test_normal_order_truncation_still_works(self, rc):
        """#29 既有能力回归：超金额截断。"""
        d = rc._check_etf_conv({"volume": 100000, "price": 10.0})  # 100 万 > 10 万上限
        assert d.adjusted is not None and d.adjusted["volume"] == 10000
