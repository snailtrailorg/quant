"""风控中心 —— 全局总风控 + 分市场独立风控双层。

所有自动交易下单前必过 check_order；一键熔断状态存 Valkey（禁止内存缓存）。
"""

from __future__ import annotations
from src.data_platform.db import get_conn
import os
import time
import logging
from dataclasses import dataclass
from typing import Literal
import redis
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("risk_control")

Level = Literal["info", "warn", "critical"]


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    severity: Level = "info"
    adjusted: dict | None = None  # B8 风控覆写（修正后的 order，如超仓位截断 volume；None=不覆写）


@dataclass
class RiskState:
    halted: bool
    total_drawdown: float
    daily_loss: float
    available: bool = True  # SB1（F-29）：False=快照数据源故障，check_order 必须 fail-closed 拒单


# ——— 风控规则配置（默认，可 Web 改） ———

DEFAULT_RULES = {
    "global": {
        "max_drawdown": 0.15,       # 总回撤 15%
        "daily_loss_limit": 0.05,  # 单日亏损 5%
    },
    "etf_conv": {
        "single_position_pct": 0.15,  # 单标的仓位 15%
        "max_trades_per_day": 20,
        "strict_stop_loss": True,
        "max_single_amount": 100000,  # 单笔金额上限（#29 风控覆写：超限截断 volume）
    },
    "crypto": {
        "leverage_max": 5,
        "margin_mode": "isolated",
        "pin_protection": True,
        "daily_loss_limit": 0.05,
    },
}


class RiskControl:
    """风控中心单例。熔断状态永远直读 Valkey，禁止内存缓存。"""

    _instance = None

    def __init__(self):
        self._redis = redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True,
        )
        # SB2（F-30/F-23）：DEFAULT 兜底合并保证三个 key 永远存在（部分规则不再 KeyError 杀事件线程）；
        # _rules_loaded_at 支撑 60s 热加载（Web 改规则对长活进程生效）
        self._rules = self._merged_rules(self._load_rules_from_db())
        self._rules_loaded_at = time.time()
        self._HALT_KEY = "risk:halted"
        self._HALT_REASON_KEY = "risk:halt_reason"

    @staticmethod
    def _merged_rules(loaded: dict | None) -> dict:
        """DEFAULT 与 DB 规则按键合并，保证 global/etf_conv/crypto 三 key 完整。"""
        loaded = loaded or {}
        return {k: {**base, **loaded.get(k, {})} for k, base in DEFAULT_RULES.items()}

    def _maybe_reload_rules(self, ttl: float = 60.0) -> None:
        """SB2（F-23）：每 60s 重读规则，Web 修改对运行中进程生效。加载失败保留旧规则（有规则好过没规则）。"""
        if time.time() - self._rules_loaded_at < ttl:
            return
        self._rules_loaded_at = time.time()
        try:
            fresh = self._load_rules_from_db()
            if fresh is not None:
                self._rules = self._merged_rules(fresh)
        except Exception as e:
            logger.warning("风控规则热加载失败（沿用旧规则）: %s", e)

    @staticmethod
    def _load_rules_from_db():
        """PI2：从 risk_rules DB 读参数（type=global/etf_conv/crypto）。无则 fallback DEFAULT_RULES。

        注意：RiskRule 接口（PT6，type=max_position 等单规则）独立，保留新规则扩展。
        risk_control 用 dict 参数（global/etf_conv/crypto），与 RiskRule 单规则抽象不同，
        故 risk_control 自己读 risk_rules（type=global/etf_conv/crypto），不用 load_rules_from_db。
        """
        import json
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute("SELECT type, params FROM risk_rules WHERE enabled=true")
                rules = {}
                for r in cur.fetchall():
                    if r[0] in ("global", "etf_conv", "crypto"):
                        rules[r[0]] = json.loads(r[1]) if r[1] else {}
            return rules if rules else None
        except Exception:
            return None

    @classmethod
    def get(cls) -> "RiskControl":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 熔断（永远直读 Valkey） ──

    def is_halted(self) -> bool:
        """⚠️ 永远直读 Valkey，禁止 self._halted 内存缓存。"""
        return self._redis.get(self._HALT_KEY) == "1"

    def emergency_halt(self, reason: str = "manual") -> None:
        """一键熔断：停止所有自动开仓。"""
        self._redis.set(self._HALT_KEY, "1")
        self._redis.set(self._HALT_REASON_KEY, reason)
        logger.critical(f"熔断触发: {reason}")

    def resume(self) -> None:
        """恢复交易（仅 Admin）。"""
        self._redis.delete(self._HALT_KEY, self._HALT_REASON_KEY)
        logger.info("熔断解除，恢复交易")

    def halt_reason(self) -> str | None:
        return self._redis.get(self._HALT_REASON_KEY)

    # ── 实盘开关（三级 AND：.env 总闸 + Web 分项 + 策略级） ──

    def _market_of(self, symbol: str) -> str | None:
        """从 vt_symbol 判实盘分项市场。
        返回 convertible/etf/astock/binance_perp/okx_perp；未知品种返回 None（拒单）。
        兼容项目 SHSE/SZSE 与 vnpy SSE/SZSE 后缀。A 股股票走 astock 分项（中泰 XTP 通道）。
        """
        if ".BINANCE" in symbol:
            return "binance_perp"
        if ".OKX" in symbol:
            return "okx_perp"
        code = symbol.split(".")[0]
        if any(symbol.endswith(s) for s in (".SHSE", ".SZSE", ".SSE")):
            if code.startswith(("11", "12")):   # 沪/深可转债
                return "convertible"
            if code.startswith(("51", "15", "56", "58")):  # ETF（沪51/深15/跨市56/科创58，SB3-F-42 补 58 防绕分项开关）
                return "etf"
            return "astock"  # A 股股票（60/00/30 开头），走 XTP astock 分项
        return None

    def is_live_trading_allowed(self, market: str) -> bool:
        """三级 AND 第二级：.env 总闸 AND Web 分项（live_trading_config 表）。
        策略级第三级（strategy_config.enabled+backtest_verified）在策略层/scheduler 检查。
        分项：convertible/etf/astock/binance_perp/okx_perp。
        """
        from src.data_platform.settings import is_live_trading_enabled
        if not is_live_trading_enabled():
            return False
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT enabled FROM live_trading_config WHERE market=%s", (market,))
                row = cur.fetchone()
            return bool(row and row[0])
        except Exception as e:
            logger.warning("is_live_trading_allowed 检查异常: %s", e)
            return False

    # ── 前置校验 ──

    def check_order(self, order: dict, account: str = "") -> RiskDecision:
        """所有自动交易 send_order 前必调。"""
        # SB2（F-23）：规则热加载（60s TTL，失败沿用旧规则）
        self._maybe_reload_rules()
        # 1. 熔断检查
        try:
            halted = self.is_halted()
        except Exception as e:
            # Valkey 不可达：按熔断处理（SB2，更保守——fail-closed）
            logger.error("熔断状态读取失败，按熔断拒单: %s", e)
            return RiskDecision(approved=False, reason=f"熔断状态不可读（Valkey 故障），保守拒单: {e}", severity="critical")
        if halted:
            return RiskDecision(approved=False, reason=f"熔断中: {self.halt_reason()}", severity="critical")

        # 2. 实盘开关（三级 AND：.env 总闸 + Web 分项 live_trading_config）
        symbol = order.get("symbol", "")
        market = self._market_of(symbol)
        if market is None:
            return RiskDecision(approved=False, reason=f"未授权实盘品种或 A 股只读: {symbol}", severity="critical")
        if not self.is_live_trading_allowed(market):
            return RiskDecision(approved=False, reason=f"实盘开关未开: {market}（需 .env ENABLE_LIVE_TRADING=true 且 Web 分项开启）", severity="warn")

        # 3. 全局风控
        state = self._get_global_state(account)
        # SB1（F-29）fail-closed：快照数据源故障/无数据时拒绝一切新单（故障时保护必须更紧不能更松）
        if not state.available:
            return RiskDecision(approved=False, reason="风控状态不可用（快照数据源故障或无数据，fail-closed），等待快照恢复", severity="critical")
        global_rules = self._rules.get("global", {})
        if state.total_drawdown >= global_rules.get("max_drawdown", 0.15):
            return RiskDecision(approved=False, reason=f"总回撤 {state.total_drawdown:.1%} 超限", severity="critical")
        if state.daily_loss >= global_rules.get("daily_loss_limit", 0.05):
            # SB2（F-31）：日亏限额只禁开仓，SELL/平仓放行——否则触发限额后连止损都做不了
            if str(order.get("action", "")).upper() != "SELL":
                return RiskDecision(approved=False, reason=f"单日亏损 {state.daily_loss:.1%} 超限，禁止开仓（平仓放行）", severity="warn")

        # 4. 分市场检查
        if ".BINANCE" in symbol or ".OKX" in symbol or "PERP" in symbol:
            return self._check_crypto(order)
        elif ".SHSE" in symbol or ".SZSE" in symbol or ".SSE" in symbol:
            return self._check_etf_conv(order)
        return RiskDecision(approved=True, reason="通过")

    def _check_etf_conv(self, order: dict) -> RiskDecision:
        """场内（可转债/ETF）风控。"""
        rules = self._rules["etf_conv"]  # _merged_rules 保证存在（SB2-F-30）
        # SB3（F-43/F-28 风控层兜底）：数量/价格无效直接拒——0 值单绕过金额上限且是废单
        price = float(order.get("price", 0) or 0)
        volume = float(order.get("volume", 0) or 0)
        if volume <= 0:
            return RiskDecision(approved=False, reason=f"委托数量无效: {volume}", severity="critical")
        if price <= 0:
            return RiskDecision(approved=False, reason="委托价格无效（缺失则无法评估金额，fail-closed）", severity="critical")
        # SC3（F-28 频次护栏）：max_trades_per_day 实装——按 order_log 当日有效单计数
        strategy_id = order.get("strategy_id")
        max_trades = rules.get("max_trades_per_day", 0)
        if strategy_id and max_trades:
            try:
                import datetime as _dt
                today = _dt.datetime.now().strftime('%Y-%m-%d')
                with get_conn() as conn:
                    cur = conn.execute(
                        "SELECT COUNT(*) FROM order_log WHERE strategy_id=%s AND ts::date=%s "
                        "AND status IN ('submitting','submitted','part_filled','filled')",
                        (strategy_id, today))
                    n = cur.fetchone()[0]
                if n >= max_trades:
                    return RiskDecision(approved=False,
                                        reason=f"当日已下 {n} 单达上限 {max_trades}（max_trades_per_day）",
                                        severity="warn")
            except Exception as e:
                # 计数失败 fail-closed（PG 故障时 _get_global_state 已先拒，这里是双保险）
                logger.warning("max_trades_per_day 计数失败（fail-closed 拒单）: %s", e)
                return RiskDecision(approved=False, reason=f"交易频次校验失败: {e}", severity="critical")
        # #29 风控覆写：单笔金额超限截断 volume（不只 reject，能修正）
        max_amount = rules.get("max_single_amount", 100000)
        amount = price * volume
        if price > 0 and amount > max_amount:
            new_vol = int(max_amount / price)
            if new_vol <= 0:
                return RiskDecision(approved=False, reason=f"单笔金额 {amount:.0f} 超限 {max_amount}，截断后 volume=0", severity="warn")
            adjusted = {**order, "volume": new_vol}
            return RiskDecision(approved=True, reason=f"单笔金额 {amount:.0f} 超限，截断 volume {int(volume)}->{new_vol}", adjusted=adjusted, severity="warn")
        return RiskDecision(approved=True, reason="场内风控通过")

    def _check_crypto(self, order: dict) -> RiskDecision:
        """加密专属风控（P3-9 补全：杠杆+逐仓+日亏损+单笔金额）。"""
        rules = self._rules["crypto"]
        leverage = order.get("leverage", 1)
        if leverage > rules["leverage_max"]:
            return RiskDecision(approved=False, reason=f"杠杆 {leverage}x 超上限 {rules['leverage_max']}x", severity="warn")
        # 日亏损熔断
        daily_limit = rules.get("daily_loss_limit", 0.05)
        state = self._get_global_state("")
        if state.daily_loss >= daily_limit:
            return RiskDecision(approved=False, reason=f"加密日亏损 {state.daily_loss:.1%} 超限 {daily_limit:.0%}", severity="critical")
        # 单笔金额截断（P3-9 补全，复用 max_single_amount）
        price = float(order.get("price", 0) or 0)
        volume = float(order.get("volume", 0) or 0)
        max_amount = rules.get("max_single_amount", 500000)
        amount = price * volume
        if price > 0 and amount > max_amount:
            new_vol = int(max_amount / price)
            if new_vol <= 0:
                return RiskDecision(approved=False, reason=f"单笔金额 {amount:.0f} 超限 {max_amount}", severity="warn")
            return RiskDecision(approved=True, reason=f"单笔截断 {int(volume)}->{new_vol}", adjusted={**order, "volume": new_vol}, severity="warn")
        return RiskDecision(approved=True, reason="加密风控通过")

    # ── 全局状态（从数据中台/账户读取，简化） ──

    def _get_global_state(self, account: str) -> RiskState:
        """获取账户全局风控状态：从 PG 读最新快照计算回撤/亏损。

        SB1（F-29/F-34）fail-closed：数据源故障或无任何快照 → available=False，
        check_order 据此拒单——绝不允许"故障时限制归零继续交易"。
        """
        try:
            with get_conn() as conn:
                # 读最新快照
                cur = conn.execute(
                    "SELECT total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 1")
                row = cur.fetchone()
                if not row:
                    logger.warning("account_snapshot 无数据（风控 fail-closed，等待首个快照）")
                    return RiskState(halted=self.is_halted(), total_drawdown=0.0, daily_loss=0.0, available=False)
                total_value, daily_pnl, initial = float(row[0]), float(row[1] or 0), float(row[2])
                # 总回撤 = (初始资金 - 当前总值) / 初始资金
                drawdown = max(0, (initial - total_value) / initial) if initial > 0 else 0
                # 单日亏损 = |daily_pnl| / 初始资金（亏损为正）
                daily_loss = abs(min(0, daily_pnl)) / initial if initial > 0 else 0
                return RiskState(halted=self.is_halted(), total_drawdown=drawdown, daily_loss=daily_loss)
        except Exception as e:
            logger.error("风控全局状态读取失败（fail-closed 拒单）: %s", e)
            try:
                halted = self.is_halted()
            except Exception:
                halted = True  # Valkey 也挂：按熔断（最保守）
            return RiskState(halted=halted, total_drawdown=0.0, daily_loss=0.0, available=False)

    def update_account_snapshot(self, total_value: float, daily_pnl: float = 0,
                                 initial_capital: float = 1_000_000):
        """更新账户快照（策略引擎/交易引擎调用，供风控读取）。"""
        import os
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital) VALUES (%s,%s,%s)",
                (total_value, daily_pnl, initial_capital))
            conn.commit()

    # ── 规则管理 ──

    def get_rules(self) -> dict:
        return dict(self._rules)

    def update_rules(self, rules: dict) -> None:
        """更新风控规则（Admin）。"""
        allowed_keys = {"global", "etf_conv", "crypto"}
        for k in rules:
            if k not in allowed_keys:
                raise ValueError(f"非法规则键: {k}")
        self._rules.update(rules)
        logger.info(f"风控规则更新: {rules}")