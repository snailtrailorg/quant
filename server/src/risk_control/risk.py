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
    rule: str | None = None  # 批19：命中规则码（UPPER_SNAKE；拒单/覆写带码、纯放行 None）——risk_log.rule 溯源


@dataclass
class RiskState:
    halted: bool
    total_drawdown: float
    daily_loss: float
    available: bool = True  # SB1（F-29）：False=快照数据源故障，check_order 必须 fail-closed 拒单


# ——— 风控规则配置（默认，可 Web 改） ———

from src.risk_control.risk_schema import RuleSanitizer   # 批36a-1（模块级——去抖指纹跨热加载持久，每进程每因一次）

_SANITIZER = RuleSanitizer(logging.getLogger("risk_control.sanitize"))

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
            socket_timeout=2, socket_connect_timeout=2)   # 批27-2：is_halted 在下单主路径——Valkey 挂起不冻下单线程（超时异常走 check_order 既有 fail-closed 拒单）
        # SB2（F-30/F-23）：DEFAULT 兜底合并保证三个 key 永远存在（部分规则不再 KeyError 杀事件线程）；
        # _rules_loaded_at 支撑 60s 热加载（Web 改规则对长活进程生效）
        # 批36a-1：Sanitizer 三层钳位（非 dict 整组回落/键类型非法回落缺省/数值超界钳边界）——
        # __init__ 载入路径先于一切，init+热加载+存量脏行三面全兜（原 TypeError 崩溃路径闭环）
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
        批36a-1：逐 type 经 Sanitizer 清洗（坏 JSON 该组回落/类型非法该键回落/超界钳位），
        告警按值指纹去抖（每进程一次，仅日志）。"""
        import json
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute("SELECT type, params FROM risk_rules WHERE enabled=true")
                rules = {}
                for r in cur.fetchall():
                    if r[0] in ("global", "etf_conv", "crypto"):
                        try:
                            raw = json.loads(r[1]) if r[1] else {}
                        except (json.JSONDecodeError, TypeError):
                            _SANITIZER._warn_once((r[0], "__badjson__"),
                                f"type={r[0]} params 非法 JSON，整组回落默认")   # B 盲审 P2-7：去抖（原 60s 热加载每分钟一响）
                            continue   # 逐 type 回落（B-P1-7：非整表）
                        rules[r[0]] = _SANITIZER.sanitize_type(r[0], raw)
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

    def _symbol_exposure(self, symbol: str, account_id) -> tuple[float, float]:
        """标的当前市值与账户总值（single_position_pct 判定用；快照缺失返回 (0,0)=不拦）。

        D2：per-account 过滤（account_id=None 时 `account_id=NULL` 恒 false → 返回 0 不拦）。
        D4：净敞口（direction_long 正 / direction_short 负），原无 direction 过滤 short 行误并入市值。
        """
        try:
            from ..data_platform.db import get_conn
            from ..data_platform.schema import to_vt_symbol
            vt = to_vt_symbol(symbol)
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT COALESCE(SUM(CASE WHEN direction='direction_long' THEN cost_price*volume "
                    "WHEN direction='direction_net' THEN cost_price*volume "
                    "WHEN direction='direction_short' THEN -cost_price*volume ELSE 0 END),0) "
                    "FROM position_snapshot WHERE symbol=%s AND account_id=%s", (vt, account_id))
                held = float(cur.fetchone()[0] or 0)
                cur = conn.execute(
                    "SELECT total_value FROM account_snapshot WHERE account_id=%s ORDER BY ts DESC LIMIT 1",
                    (account_id,))
                row = cur.fetchone()
                return held, float(row[0]) if row else 0.0
        except Exception:
            return 0.0, 0.0

    def _market_of(self, symbol: str) -> str | None:
        """从 vt_symbol 判实盘分项市场（D4 起委托层0 quant_common.fees.symbol_market 单源）。

        返回 convertible/etf/astock/binance_perp/okx_perp；未知品种返回 None（拒单）。
        兼容项目 SHSE/SZSE 与 vnpy SSE/SZSE 后缀。A 股股票走 astock 分项（中泰 XTP 通道）。
        """
        from src.quant_common.fees import symbol_market
        return symbol_market(symbol)

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

    @staticmethod
    def _role_of(username: str) -> str | None:
        """operator=username → users.role（批15 market_op 判定用）。

        筛 enabled+未软删：软删/禁用用户无命中=None（调用方按 deny 处理，fail-closed）。
        读库异常向上抛（由 2.5 整段 try 收口成 deny critical——拒绝可见）。
        """
        with get_conn() as conn:
            row = conn.execute(
                "SELECT role FROM users WHERE username=%s "
                "AND enabled AND deleted_at IS NULL", (username,)).fetchone()
        return row[0] if row else None

    def check_order(self, order: dict, account_id=None) -> RiskDecision:
        """所有自动交易 send_order 前必调。

        P1-1（web-design 06 B#2）：出口统一落 risk_log（approve/reject/adjust 可筛）——
        风控页决策面板数据源。写库失败只 warning 不阻断下单路径（日志是审计面非控制面）。
        D2：account_id 为读方/写方 per-account 过滤真源（order 内 "account_id" 键同源，由 place_order 注入）。
        """
        d = self._check_order_inner(order, account_id)
        try:
            # 批19 盲审A-P1 修：按 adjusted 语义判（原 "截断" in reason——场内"截断后 volume=0"
            # 的拒单被记 adjust、真覆写(approved+adjusted)被记 approve，同码横跨三 action）
            action = "adjust" if d.adjusted is not None else ("approve" if d.approved else "reject")
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO risk_log (action, symbol, rule, detail, severity) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (action, order.get("symbol", ""), d.rule if hasattr(d, "rule") else None,
                     d.reason, d.severity))
                conn.commit()
        except Exception as e:
            logger.warning("risk_log 写入失败（不阻断）: %s", e)
        return d

    def _check_order_inner(self, order: dict, account_id=None) -> RiskDecision:
        # SB2（F-23）：规则热加载（60s TTL，失败沿用旧规则）
        self._maybe_reload_rules()
        # 1. 熔断检查
        try:
            halted = self.is_halted()
            _halt_reason = self.halt_reason() if halted else None   # 批27-2：并进 try——超时也有 risk_log 落迹而非裸抛
        except Exception as e:
            # Valkey 不可达：按熔断处理（SB2，更保守——fail-closed）
            logger.error("熔断状态读取失败，按熔断拒单: %s", e)
            return RiskDecision(approved=False, reason=f"熔断状态不可读（Valkey 故障），保守拒单: {e}", severity="critical", rule="HALT_UNREADABLE")
        if halted:
            return RiskDecision(approved=False, reason=f"熔断中: {_halt_reason}", severity="critical", rule="HALTED")

        # 2. 实盘开关（三级 AND：.env 总闸 + Web 分项 live_trading_config）
        symbol = order.get("symbol", "")
        market = self._market_of(symbol)
        if market is None:
            return RiskDecision(approved=False, reason=f"未授权实盘品种或 A 股只读: {symbol}", severity="critical", rule="MARKET_UNAUTHORIZED")
        if not self.is_live_trading_allowed(market):
            return RiskDecision(approved=False, reason=f"实盘开关未开: {market}（需 .env ENABLE_LIVE_TRADING=true 且 Web 分项开启）", severity="warn", rule="LIVE_SWITCH_OFF")

        # 2.5 市场操作权限（批15 market_op 维；SELL/平仓完全豁免——F-31 同哲学：准入拦
        # 开仓，平仓=减风险方向；operator 缺失的 SELL 同样放行，旧 --id 路径持仓可止损。
        # 整段 try 收口：异常=deny critical 落 risk_log，保持"拒绝可见"）
        try:
            from src.data_platform.perms import market_op_allowed
            operator = order.get("operator") or ""
            if str(order.get("action", "")).upper() != "SELL":
                if not operator:
                    return RiskDecision(approved=False,
                        reason="订单缺 operator（旧 --id 路径或异常构造，fail-closed 拒单）",
                        severity="critical", rule="OPERATOR_MISSING")
                role_of = self._role_of(operator)
                if role_of is None or not market_op_allowed(operator, role_of, market):
                    return RiskDecision(approved=False,
                        reason=f"市场操作权限拒绝: {market}（operator={operator}）", severity="warn", rule="MARKET_OP_DENIED")
        except Exception as e:
            return RiskDecision(approved=False,
                reason=f"市场操作权限检查异常（fail-closed）: {e}", severity="critical", rule="MARKET_OP_ERROR")

        # 2.6 D5 三级时点③：account 级品种权限（account_allows）——仅拦 BUY/开仓，SELL 豁免（D1 裁定）。
        # account_id=None（旧 --id 路径/异常）跳过本段，由全局风控 _get_global_state(None) 读方
        # fail-closed（WHERE account_id=NULL 恒 false → available=False 拒单）兜住。
        try:
            if str(order.get("action", "")).upper() == "BUY" and account_id is not None:
                from src.data_platform.perms import account_allows
                if not account_allows(account_id, symbol):
                    return RiskDecision(approved=False,
                        reason=f"account 级品种权限拒绝: {symbol}（account_id={account_id}）",
                        severity="warn", rule="ACCOUNT_NOT_ALLOWED")
        except Exception as e:
            return RiskDecision(approved=False,
                reason=f"account 权限检查异常（fail-closed）: {e}", severity="critical", rule="ACCOUNT_PERM_ERROR")

        # 3. 全局风控
        state = self._get_global_state(account_id)
        # SB1（F-29）fail-closed：快照数据源故障/无数据时拒绝一切新单（故障时保护必须更紧不能更松）
        if not state.available:
            return RiskDecision(approved=False, reason="风控状态不可用（快照数据源故障或无数据，fail-closed），等待快照恢复", severity="critical", rule="SNAPSHOT_UNAVAILABLE")
        global_rules = self._rules.get("global", {})
        if state.total_drawdown >= global_rules.get("max_drawdown", 0.15):
            # P0 修复（2026-08-20 双盲审计 F5.2）：总回撤熔断曾连 SELL/止损一起拒——触线后
            # 只能持仓看戏，与 F-31 同错。对齐日亏语义：只禁开仓，平仓放行。
            if str(order.get("action", "")).upper() != "SELL":
                return RiskDecision(approved=False, reason=f"总回撤 {state.total_drawdown:.1%} 超限，禁止开仓（平仓放行）", severity="critical", rule="MAX_DRAWDOWN")
        if state.daily_loss >= global_rules.get("daily_loss_limit", 0.05):
            # SB2（F-31）：日亏限额只禁开仓，SELL/平仓放行——否则触发限额后连止损都做不了
            if str(order.get("action", "")).upper() != "SELL":
                return RiskDecision(approved=False, reason=f"单日亏损 {state.daily_loss:.1%} 超限，禁止开仓（平仓放行）", severity="warn", rule="DAILY_LOSS_LIMIT")

        # 4. 分市场检查
        if ".BINANCE" in symbol or ".OKX" in symbol or "PERP" in symbol:
            return self._check_crypto(order, state)
        elif ".SHSE" in symbol or ".SZSE" in symbol or ".SSE" in symbol:
            return self._check_etf_conv(order, account_id)
        return RiskDecision(approved=True, reason="通过")

    def _check_etf_conv(self, order: dict, account_id) -> RiskDecision:
        """场内（可转债/ETF）风控。account_id 来自 _check_order_inner 形参（单一真源，勿用 order 键分叉）。"""
        rules = self._rules["etf_conv"]  # _merged_rules 保证存在（SB2-F-30）
        # SB3（F-43/F-28 风控层兜底）：数量/价格无效直接拒——0 值单绕过金额上限且是废单
        price = float(order.get("price", 0) or 0)
        volume = float(order.get("volume", 0) or 0)
        if volume <= 0:
            return RiskDecision(approved=False, reason=f"委托数量无效: {volume}", severity="critical", rule="INVALID_VOLUME")
        if price <= 0:
            return RiskDecision(approved=False, reason="委托价格无效（缺失则无法评估金额，fail-closed）", severity="critical", rule="INVALID_PRICE")
        # SC3（F-28 频次护栏）：max_trades_per_day 实装——按 order_log 当日有效单计数
        strategy_id = order.get("strategy_id")
        max_trades = rules.get("max_trades_per_day", 0)
        if strategy_id and max_trades:
            try:
                import datetime as _dt
                today = _dt.datetime.now().strftime('%Y-%m-%d')
                with get_conn() as conn:
                    cur = conn.execute(
                        "SELECT COUNT(*) FROM order_log WHERE strategy_id=%s AND (ts AT TIME ZONE 'Asia/Shanghai')::date=%s "
                        "AND status IN ('submitting','submitted','part_filled','filled')",
                        (strategy_id, today))
                    n = cur.fetchone()[0]
                if n >= max_trades:
                    return RiskDecision(approved=False,
                                        reason=f"当日已下 {n} 单达上限 {max_trades}（max_trades_per_day）",
                                        severity="warn", rule="MAX_TRADES_PER_DAY")
            except Exception as e:
                # 计数失败 fail-closed（PG 故障时 _get_global_state 已先拒，这里是双保险）
                logger.warning("max_trades_per_day 计数失败（fail-closed 拒单）: %s", e)
                return RiskDecision(approved=False, reason=f"交易频次校验失败: {e}", severity="critical", rule="TRADE_COUNT_ERROR")
        # P2 修复（2026-08-20 双盲审计 F5.1）：single_position_pct 原只有默认值零判定——
        # 规则表展示存在但闸不存在（认知误导）。实装：BUY 后标的市值/账户总值 超限拒单。
        if str(order.get("action", "")).upper() == "BUY":
            try:
                pct_limit = float(rules.get("single_position_pct", 0.15))
                sym = order.get("symbol", "")
                held_val, total_val = self._symbol_exposure(sym, account_id)
                after = held_val + price * volume
                if total_val > 0 and after / total_val > pct_limit * 1.05:   # 5% 容差防边界抖动
                    return RiskDecision(approved=False,
                                        reason=f"单标的仓位 {after/total_val:.1%} 将超限 {pct_limit:.0%}（single_position_pct）",
                                        severity="warn", rule="SINGLE_POSITION_PCT")
            except Exception as e:
                logger.warning("single_position_pct 检查失败（放行——暴露度计算依赖快照，极端故障由 fail-closed 兜）: %s", e)
        # #29 风控覆写：单笔金额超限截断 volume（不只 reject，能修正）
        max_amount = rules.get("max_single_amount", 100000)
        amount = price * volume
        if price > 0 and amount > max_amount:
            new_vol = int(max_amount / price)
            if new_vol <= 0:
                return RiskDecision(approved=False, reason=f"单笔金额 {amount:.0f} 超限 {max_amount}，截断后 volume=0", severity="warn", rule="MAX_SINGLE_AMOUNT")
            adjusted = {**order, "volume": new_vol}
            return RiskDecision(approved=True, reason=f"单笔金额 {amount:.0f} 超限，截断 volume {int(volume)}->{new_vol}", adjusted=adjusted, severity="warn", rule="MAX_SINGLE_AMOUNT")
        return RiskDecision(approved=True, reason="场内风控通过")

    def _check_crypto(self, order: dict, state) -> RiskDecision:
        """加密专属风控（P3-9 补全：杠杆+逐仓+日亏损+单笔金额）。state 复用 _check_order_inner 已算（免二次查库）。"""
        rules = self._rules["crypto"]
        leverage = order.get("leverage", 1)
        if leverage > rules["leverage_max"]:
            return RiskDecision(approved=False, reason=f"杠杆 {leverage}x 超上限 {rules['leverage_max']}x", severity="warn", rule="LEVERAGE_MAX")
        # 日亏损熔断（复用第 3 步已算的 state.daily_loss）
        daily_limit = rules.get("daily_loss_limit", 0.05)
        if state.daily_loss >= daily_limit:
            return RiskDecision(approved=False, reason=f"加密日亏损 {state.daily_loss:.1%} 超限 {daily_limit:.0%}", severity="critical", rule="CRYPTO_DAILY_LOSS")
        # 单笔金额截断（P3-9 补全，复用 max_single_amount）
        price = float(order.get("price", 0) or 0)
        volume = float(order.get("volume", 0) or 0)
        max_amount = rules.get("max_single_amount", 500000)
        amount = price * volume
        if price > 0 and amount > max_amount:
            new_vol = int(max_amount / price)
            if new_vol <= 0:
                return RiskDecision(approved=False, reason=f"单笔金额 {amount:.0f} 超限 {max_amount}", severity="warn", rule="MAX_SINGLE_AMOUNT")
            return RiskDecision(approved=True, reason=f"单笔截断 {int(volume)}->{new_vol}", adjusted={**order, "volume": new_vol}, severity="warn", rule="MAX_SINGLE_AMOUNT")
        return RiskDecision(approved=True, reason="加密风控通过")

    # ── 全局状态（从数据中台/账户读取，简化） ──

    def _get_global_state(self, account_id) -> RiskState:
        """获取账户全局风控状态：从 PG 读该 account 最新快照计算回撤/亏损。

        SB1（F-29/F-34）fail-closed：数据源故障或无任何快照 → available=False，
        check_order 据此拒单——绝不允许"故障时限制归零继续交易"。
        D2：per-account 过滤（account_id=None 恒不命中 → available=False fail-closed）。
        """
        try:
            with get_conn() as conn:
                # 读最新快照（P1 修复 2026-08-20 双盲审计 F5.3：带新鲜度——原只看最新一条，
                # runner 死/TD 断线后风控永远用最后一帧（drawdown 冻结/daily_pnl 陈旧）=陈旧 fail-open。
                # 快照写频 60s（runner 循环），>5min 即判陈旧拒单）
                cur = conn.execute(
                    "SELECT total_value, daily_pnl, initial_capital, "
                    "EXTRACT(EPOCH FROM (now() - ts)) FROM account_snapshot "
                    "WHERE account_id=%s ORDER BY ts DESC LIMIT 1", (account_id,))
                row = cur.fetchone()
                if not row:
                    logger.warning("account_snapshot 无数据（风控 fail-closed，等待首个快照）")
                    return RiskState(halted=self.is_halted(), total_drawdown=0.0, daily_loss=0.0, available=False)
                total_value, daily_pnl, initial = float(row[0]), float(row[1] or 0), float(row[2])
                age_s = float(row[3] or 0)
                if age_s > 300:
                    logger.warning("账户快照陈旧 %.0fs（runner 死/断线？fail-closed 拒单）", age_s)
                    return RiskState(halted=self.is_halted(), total_drawdown=0.0, daily_loss=0.0, available=False)
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
                                 initial_capital: float = 1_000_000, account_id=None):
        """更新账户快照（策略引擎/交易引擎调用，供风控读取）。D2：per-account 落行。"""
        import os
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO account_snapshot (account_id, total_value, daily_pnl, initial_capital) VALUES (%s,%s,%s,%s)",
                (account_id, total_value, daily_pnl, initial_capital))
            conn.commit()

    # ── 规则管理 ──

    def get_rules(self) -> dict:
        return dict(self._rules)

    # 批39 A-P2-5：update_rules 已删（全仓零调用+绕过 RuleSanitizer 不落库的陷阱面——风险页显示恒走 60s 热加载真源）