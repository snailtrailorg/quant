"""策略框架 · Strategy 基类 + 信号聚合 + Python 代码模式。

所有策略（A股分析/可转债场内基金/加密合约）共用此基类，差异下沉到 ExecutionAdapter。
Python 代码模式（#15）允许用户写自定义 on_bar 逻辑，替代 DSL 因子组合。
"""

from __future__ import annotations
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable
from .factor import Factor, BarContext, list_factors, get_factor, DSLFactor, _FACTOR_REGISTRY, _check_ast_blacklist
from .broker import record_broker_usage

logger = logging.getLogger("strategy")


# ——— 信号定义 ———

class Action(Enum):
    BUY = 1
    SELL = 2
    HOLD = 0


@dataclass
class Signal:
    action: Action
    score: float = 0.0
    symbol: str = ""
    volume: float = 0.0
    price: float = 0.0
    reason: str = ""
    # ActionSignal 扩展（#3，策略表单可配）
    volume_type: str = "SHARES"  # SHARES（股数）/ PERCENT（资金百分比，需账户资产）/ ALL_IN（全仓）
    price_type: str = "LIMIT"    # MARKET（市价）/ LIMIT（限价）
    order_validity: str = "DAY"  # DAY / GTC


# ——— 信号聚合 ———

@dataclass
class SignalAggregator:
    weights: dict[str, float] = field(default_factory=dict)
    threshold_buy: float = 0.3
    threshold_sell: float = -0.3
    method: str = "weighted_sum"

    def aggregate(self, factor_values: dict[str, float]) -> Signal:
        """因子值 → 买卖信号。"""
        score = 0.0
        for name, val in factor_values.items():
            w = self.weights.get(name, 1.0)
            score += val * w

        if score > self.threshold_buy:
            return Signal(action=Action.BUY, score=score, reason=f"score={score:.3f} > {self.threshold_buy}")
        elif score < self.threshold_sell:
            return Signal(action=Action.SELL, score=score, reason=f"score={score:.3f} < {self.threshold_sell}")
        return Signal(action=Action.HOLD, score=score)


# ——— 策略配置 Schema ———

@dataclass
class StrategyConfig:
    id: str
    name: str
    type: str  # "astock_analysis" / "convertible_t0" / "crypto_perp"
    symbol: str
    adapter: str  # "xtp"（可转债/场内基金/A股股票，中泰XTP）/ "binance_perp" / "okx_perp"
    enabled: bool = True
    factors: list[dict] = field(default_factory=list)  # [{"name":"ma_dev","weight":0.6,"params":{}}, ...]
    aggregator: dict = field(default_factory=lambda: {"method":"weighted_sum","threshold_buy":0.3,"threshold_sell":-0.3})
    risk: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)


# ——— Strategy 基类 ———

# --- 参数定义系统（parameter_defs 校验 + 默认值合并） ---

_PARAM_TYPES = {"number", "boolean", "string", "select"}


def validate_parameter_defs(defs):
    """校验 parameter_defs 结构。返回错误描述，None 表示合法。"""
    if not isinstance(defs, list):
        return "parameter_defs 必须是数组"
    names = set()
    for d in defs:
        if not isinstance(d, dict):
            return "参数定义必须是对象"
        name = d.get("name")
        if not name or not isinstance(name, str):
            return "参数定义缺 name 字段"
        if name in names:
            return f"参数名重复: {name}"
        names.add(name)
        ptype = d.get("type", "number")
        if ptype not in _PARAM_TYPES:
            return f"参数 {name} 类型不支持: {ptype}（可选: {', '.join(_PARAM_TYPES)}）"
        if "default" not in d:
            return f"参数 {name} 缺 default 字段"
        if ptype == "select" and not d.get("options"):
            return f"select 参数 {name} 缺 options"
    return None


def build_default_params(defs):
    """从 parameter_defs 构建默认参数值 dict。"""
    result = {}
    for d in (defs or []):
        v = d.get("default")
        if v is None:
            continue
        result[d["name"]] = v
    return result


def validate_params_against_defs(params, defs):
    """按 parameter_defs 校验参数值类型/范围。返回错误描述，None 表示合法。"""
    defs_by_name = {d["name"]: d for d in (defs or [])}
    for name, val in params.items():
        if name == "parameter_defs":
            continue
        if name not in defs_by_name:
            continue
        d = defs_by_name[name]
        ptype = d.get("type", "number")
        if ptype == "number":
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                return f"参数 {name} 必须是数字，实际: {type(val).__name__}"
            if "min" in d and val < d["min"]:
                return f"参数 {name}={val} 低于最小值 {d['min']}"
            if "max" in d and val > d["max"]:
                return f"参数 {name}={val} 超过最大值 {d['max']}"
        elif ptype == "boolean":
            if not isinstance(val, bool):
                return f"参数 {name} 必须是布尔，实际: {type(val).__name__}"
        elif ptype == "string":
            if not isinstance(val, str):
                return f"参数 {name} 必须是字符串，实际: {type(val).__name__}"
        elif ptype == "select":
            valid_vals = [o.get("value") for o in d.get("options", [])]
            if val not in valid_vals:
                return f"参数 {name}={val} 不在可选项 {valid_vals}"
    return None


class Strategy:
    """统一策略基类。所有策略（A股分析/可转债场内基金/加密合约）继承此基类。"""

    def __init__(self, config: StrategyConfig, adapter):
        self.id = config.id
        self.symbol = config.symbol
        self.config = config
        self.adapter = adapter
        self._factors: list[Factor] = []
        self._aggregator = SignalAggregator(
            weights={f["name"]: f.get("weight", 1.0) for f in config.factors},
            method=config.aggregator.get("method", "weighted_sum"),
            threshold_buy=config.aggregator.get("threshold_buy", 0.3),
            threshold_sell=config.aggregator.get("threshold_sell", -0.3),
        )
        self._init_factors(config.factors)

    def log(self, msg: str, level: str = "info") -> None:
        """策略作者日志入口（ptrade 批 1）：回测时进 run 作用域日志，实盘进进程日志。"""
        try:
            fn = getattr(logger, str(level).lower(), None)
        except Exception:
            fn = None
        (fn if callable(fn) else logger.info)(msg)

    def _init_factors(self, factor_configs: list[dict]):
        """从配置初始化因子实例。"""
        for fc in factor_configs:
            name = fc["name"]
            if name.startswith("dsl:"):
                # DSL 表达式因子
                expr = fc.get("expr", "")
                factor = DSLFactor(name, expr)
            else:
                entry = get_factor(name)
                if entry is None:
                    raise ValueError(f"未知因子: {name}")
                factor = entry["cls"]()
                factor.name = name
                factor.params = {**entry["params"], **fc.get("params", {})}
            self._factors.append(factor)

    # ——— 核心回调 ———

    def on_bar(self, bar: dict, history: list[dict] | None = None) -> Signal | None:
        """收到 K 线 → 因子计算 → 信号 → 下单。"""
        ctx = BarContext(
            close=bar.get("close", 0),
            high=bar.get("high", 0),
            low=bar.get("low", 0),
            open_=bar.get("open", 0),
            volume=bar.get("volume", 0),
            history=history or [],
            symbol=self.symbol,          # P2-2026-08-20：double_low 等跨表因子接通
            bar_ts=bar.get("ts"),        # P2：时点约束（防回测前视）
        )
        fv = self.compute_factors(ctx)
        sig = self._aggregator.aggregate(fv)
        sig.symbol = self.symbol
        if sig.action != Action.HOLD:
            # 链条打磨#2/#12（2026-08-19）：聚合信号回填执行规则——此前 volume/price 恒 0
            # → SC3 丢弃 → 因子模式实盘零下单（回测因 monkey-patch 固定 100 股假成交）
            sig.price = sig.price or ctx.close
            sig.volume = self._resolve_volume(sig, ctx.close)
            # R-S3b：执行规则接线——此前聚合信号恒默认 LIMIT/DAY，用户选 MARKET/GTC 被静默忽略
            sig.price_type = self._param("price_type", "LIMIT")
            sig.order_validity = self._param("order_validity", "DAY")
            self.place_order(sig)
        return sig

    def on_tick(self, tick: dict) -> None:
        """收到 Tick → 实时处理。"""
        pass

    def _is_crypto(self) -> bool:
        """判断是否为加密市场（无 A 股整百手约束）。

        反向判断（盲审遗留 2026-08-22）：原白名单 in ("binance_perp","okx_perp") 在新增
        加密适配器时会静默回落 A 股整百取整--小单截断恒 0 直接丢单（盲审 A-1 同族失效模式）；
        反向 != "xtp" 的失效模式是未知适配器拿到 float 量、由网关显式拒单（响亮失败），
        方向更安全。
        """
        return self.config.adapter != "xtp"

    def _resolve_volume(self, sig: Signal, price: float) -> float:
        """执行规则三档（#12，R-F1 修订：方向感知资金口径）。

        BUY  = 可用资金口径（总资产−持仓市值；满仓→0 → SC3 拦截，不下废单）
        SELL = 持仓口径（本 symbol 持仓量；PERCENT=持仓×pct / ALL_IN=全部持仓）
               ——SELL ALL_IN 的"全仓"语义=清仓该标的，不是现金÷price（R-F1：原实现
               会让卖单量>持仓→XTP 拒单→止损保护失效）
        持仓来源 position_snapshot（ST2 真相源，direction != 'short'）。查询失败 →
        告警+降级 SHARES 100（不拒单，风险随信号下发）。
        """
        from enum import Enum as _E
        vt = self._param("volume_type", "SHARES")
        is_sell = getattr(getattr(sig, "action", None), "name", "") == "SELL"
        try:
            if vt in ("PERCENT", "ALL_IN"):
                if is_sell:
                    held = self._held_volume()
                    if held <= 0:
                        return 0   # 无持仓可卖——SC3 拦截（非静默）
                    if vt == "ALL_IN":
                        return held
                    pct = float(self._param("volume_pct", 10)) / 100.0
                    if self._is_crypto():
                        return max(0.0, held * pct)   # crypto float（盲审 A-1：int 截断小持仓恒 0）
                    return max(0, int(held * pct / 100) * 100)   # A股整百
                # BUY：可用资金口径——优先快照 available_cash（DB 优化批 2026-08-21，审计 F4.1：
                # 原总资产-持仓成本近似在多策略共账户时合计超配）；无该列数据退化旧口径
                avail_cash = self._available_cash()
                if avail_cash is not None and avail_cash >= 0:
                    cash = avail_cash
                else:
                    total = self._latest_total_value()
                    held_value = self._held_value()
                    cash = max(0.0, total - held_value)
                if vt == "ALL_IN":
                    base = cash
                else:
                    base = cash * (float(self._param("volume_pct", 10)) / 100.0)
                if self._is_crypto():
                    return max(0.0, base / price) if price > 0 else 0   # crypto float（盲审 A-1）
                return max(0, int(base / price / 100) * 100) if price > 0 else 0
        except Exception as e:
            logger.warning("PERCENT/ALL_IN 资产/持仓查询失败，降级 SHARES 100: %s", e)
        return float(self._param("volume", 100))

    def _held_volume(self) -> int:
        """本 symbol **可卖**持仓量（ST2 position_snapshot 真相源；SELL 口径）。

        P0-4 修复（2026-08-20 双盲审计 S2）：原 SUM(volume) 含冻结仓——T+1 当日买入量
        计入 SELL 口径 → ALL_IN 超卖 → 交易所拒单 → 止损失效（R-F1 要防的正是这个）。
        可卖 = volume - frozen（快照列语义）；frozen 缺行时退化 volume-yd_volume 不可靠，
        直接 volume - COALESCE(frozen,0)。
        """
        from ..data_platform.db import get_conn
        from ..data_platform.schema import to_vt_symbol
        vt = self.symbol if "." in self.symbol else to_vt_symbol(self.symbol)
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT COALESCE(SUM(volume - COALESCE(frozen,0)),0) FROM position_snapshot "
                "WHERE symbol=%s AND direction != 'short'", (vt,))
            return int(cur.fetchone()[0] or 0)

    def _held_value(self) -> float:
        """持仓总市值近似（position_snapshot.pnl 与 cost_price 无市值列——用最新快照总资产差。
        R 注：近似口径，精确需行情乘持仓——成本价×量做下界近似。"""
        from ..data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT COALESCE(SUM(cost_price * volume),0) FROM position_snapshot "
                "WHERE direction != 'short'")
            return float(cur.fetchone()[0] or 0)

    def _param(self, key, default=None):
        p = self.config.params
        return (p.get(key, default) if isinstance(p, dict) else default) or default

    def _latest_total_value(self) -> float:
        from ..data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute("SELECT total_value FROM account_snapshot ORDER BY ts DESC LIMIT 1")
            row = cur.fetchone()
        if not row or not row[0]:
            raise RuntimeError("account_snapshot 无数据")
        return float(row[0])

    def _available_cash(self) -> float | None:
        """快照可用资金（DB 优化批 2026-08-21，审计 F4.1）：无该列数据/无快照返回 None
        （调用方退化旧口径 total-held 近似）。"""
        from ..data_platform.db import get_conn
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT available_cash FROM account_snapshot "
                    "WHERE available_cash IS NOT NULL ORDER BY ts DESC LIMIT 1")
                row = cur.fetchone()
            return float(row[0]) if row else None
        except Exception:
            return None

    # ——— 因子计算 ———

    def compute_factors(self, ctx: BarContext) -> dict[str, float]:
        """计算所有因子值。"""
        result = {}
        for f in self._factors:
            try:
                result[f.name] = f.compute(ctx)
            except Exception as e:
                logger.warning("因子 %s 计算失败: %s", f.name, e)
                result[f.name] = 0.0
        return result

    # ——— 下单 ———

    def place_order(self, sig: Signal) -> None:
        """下单：本地校验 → 前置风控 → WAL 记账 → 发单 → 状态流转（SC1，F-4/F-27/F-28）。

        时序铁律：submitting 先落库再发单——崩溃窗口内残留 submitting 可被启动对账认领，
        绝不允许"交易所有单、系统无痕"（F-4）。记账失败则放弃下单（fail-closed）。
        """
        from ..risk_control.risk import RiskControl  # 延迟导入
        # SC3（F-28）源头拦截：无效数量/价格的信号直接丢弃（0 值废单洪水根除）
        volume = sig.volume if sig.volume else 0
        price = sig.price if sig.price else 0
        if volume <= 0 or price <= 0:
            logger.warning("信号无效丢弃 volume=%s price=%s (%s %s): %s",
                           volume, price, self.symbol, sig.action.name, sig.reason)
            return
        # P0-4 兜底（2026-08-20 双盲审计 S1/S2）：SELL 统一截断到可卖仓——覆盖全部策略路径
        # （覆写 on_bar 的预置策略/Python 模式原本绕过 _resolve_volume 的持仓口径）。
        # T+1 当日买入冻结仓计入 → 超卖 → 交易所拒单 → 止损失效。
        if sig.action.name == "SELL":
            try:
                sellable = self._held_volume()
                if sellable < volume:   # 复审边界a：sellable==0 也截断（当日买入当日止损场景零可卖不发废单）
                    logger.warning("SELL 截断到可卖仓 %d（请求 %d，T+1 冻结/持仓不足）",
                                   sellable, volume)
                    volume = sellable
                    sig.volume = sellable
            except Exception as e:
                logger.warning("可卖仓查询失败，SELL 按原量提交（快照故障由风控 fail-closed 兜）: %s", e)
        order = {
            "symbol": self.symbol,
            "action": sig.action.name,
            "volume": volume,
            "price": price,
            "reason": sig.reason,
            "strategy_id": self.config.id,  # SC3：供 max_trades_per_day 计数
            "validity": getattr(sig, "order_validity", "DAY"),   # P2：GTC 曾读了不传（R-S3b 半接线）
        }
        decision = RiskControl.get().check_order(order, "")
        if not decision.approved:
            return
        final = decision.adjusted if decision.adjusted is not None else order  # B8 风控覆写：用 adjusted（如截断 volume），无则原值
        # WAL（F-4）：先写 signal_log + order_log(submitting)
        sig_id, order_row_id = self._log_signal_order(sig, final, status="submitting")
        if order_row_id is None:
            logger.error("WAL 记账失败，放弃本次下单（fail-closed）: %s %s", self.symbol, sig.action.name)
            return
        from .adapters import Order
        import time as _t
        if final.get("validity") == "GTC":
            logger.warning("XTP 限价单无 GTC 语义，按当日有效（DAY）发出——配置降级声明（P2）")
        _t0 = _t.time()
        try:
            client_id = self.adapter.send_order(Order(
                symbol=final.get("symbol", self.symbol),
                action=final.get("action", sig.action.name),
                volume=final.get("volume", volume),
                price=final.get("price", price),
                order_type="market" if sig.price_type == "MARKET" else "limit",
            ))
        except Exception as e:
            record_broker_usage(self.config.adapter, sig.action.name, self.symbol,
                                success=False, latency_ms=int((_t.time() - _t0) * 1000))
            self._update_order_status(order_row_id, "send_failed", error=str(e)[:500])
            raise
        # F-27：adapter 返回空委托号=委托未真实发出（gateway 对不支持的类型/断线返回 ""/伪造"0"）
        if not client_id:
            record_broker_usage(self.config.adapter, sig.action.name, self.symbol,
                                success=False, latency_ms=int((_t.time() - _t0) * 1000))
            self._update_order_status(order_row_id, "send_failed", error="adapter 返回空委托号（委托未发出）")
            logger.error("send_order 无有效委托号，标记 send_failed: %s %s", self.symbol, sig.action.name)
            return
        record_broker_usage(self.config.adapter, sig.action.name, self.symbol,
                            success=True, latency_ms=int((_t.time() - _t0) * 1000))
        # F-50：写回 vt_orderid（vnpy 委托号）——重启后 _vt2cid 丢失，write_trade_log 靠它反查 order_id
        vt_orderid = self.adapter.get_vt_orderid(client_id)
        self._update_order_status(order_row_id, "submitted", client_order_id=client_id,
                                  vt_orderid=vt_orderid)

    def _log_signal_order(self, sig: Signal, final_order: dict, status: str = "submitted") -> tuple:
        """写 signal_log + order_log（三账对账数据来源，P1-3；SC1 起带 status 流转）。

        返回 (signal_id, order_id)；失败返回 (None, None)。回测 monkey-patch 跳过。
        """
        try:
            from ..data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO signal_log (strategy_id,symbol,action,score,price) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (self.config.id, self.symbol, sig.action.name, sig.score, sig.price))
                sig_id = cur.fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO order_log (strategy_id,symbol,action,volume,price,signal_id,status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (self.config.id, self.symbol, final_order.get("action", sig.action.name),
                     final_order.get("volume", 100), final_order.get("price", 0), sig_id, status))
                order_id = cur.fetchone()[0]
                conn.commit()
                return sig_id, order_id
        except Exception as e:
            logger.warning("记录 signal/order 日志失败: %s", e)
            return None, None

    @staticmethod
    def _update_order_status(order_row_id: int, status: str,
                             client_order_id: str | None = None, error: str | None = None,
                             vt_orderid: str | None = None) -> None:
        """SC1：order_log 状态流转（submitting→submitted/send_failed；成交/撤单由 trade/事件推进）。

        F-50（2026-09-03）：加 vt_orderid（vnpy 委托号）——重启后 _vt2cid 进程内存丢失，
        write_trade_log 靠 vt_orderid 反查 order_id。client_order_id/vt_orderid 用 COALESCE
        （未传不覆盖既有值，防未来「成交/撤单」流转漏传时清空关联键），error 直接 SET（需支持清空）。
        """
        try:
            from ..data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute(
                    "UPDATE order_log SET status=%s, "
                    "client_order_id=COALESCE(%s, client_order_id), "
                    "error=%s, "
                    "vt_orderid=COALESCE(%s, vt_orderid) WHERE id=%s",
                    (status, client_order_id, error, vt_orderid, order_row_id))
                conn.commit()
        except Exception as e:
            logger.warning("更新 order_log 状态失败 (id=%s -> %s): %s", order_row_id, status, e)

    # ——— 工厂 ———

    @classmethod
    def from_config(cls, config: StrategyConfig, adapter) -> "Strategy":
        """P1-1 策略类型注册制：按 config.type 分发到注册的子类，默认基类。

        Python 代码模式（#15）优先：params.mode == "python" 时返回 PythonStrategy。
        """
        if config.params.get("mode") == "python":
            return PythonStrategy(config, adapter)
        strategy_cls = _STRATEGY_REGISTRY.get(config.type, cls)
        return strategy_cls(config, adapter)


# 策略类型注册表 + 装饰器（P1-1）
_STRATEGY_REGISTRY: dict[str, type] = {}


def register_strategy(type_name: str):
    """策略子类注册装饰器。from_config 按 type 查此表分发。"""
    def decorator(cls):
        _STRATEGY_REGISTRY[type_name] = cls
        return cls
    return decorator


# ——— Python 代码模式（#15） ———

class StrategyContext:
    """Python 代码模式中用户代码唯一可用的上下文。

    用户 on_bar 函数接收此对象，通过它访问数据、下单、管理状态。
    """

    def __init__(self, strategy_id: str, symbol: str):
        self.id = strategy_id
        self.symbol = symbol
        self._bar: dict = {}
        self._history: list[dict] = []
        self._params: dict = {}
        self._state: dict = {}
        self._signal: Signal | None = None

    def _update(self, bar: dict, history: list[dict] | None, params: dict):
        self._bar = bar
        self._history = history or []
        self._params = params
        self._signal = None
        # 构建 BarContext 供 get_factor 调用
        self._bar_ctx = BarContext(
            close=bar.get("close", 0),
            high=bar.get("high", 0),
            low=bar.get("low", 0),
            open_=bar.get("open", 0),
            volume=bar.get("volume", 0),
            history=history or [],
            symbol=self.symbol,          # P2：同基类 on_bar（get_factor 路径）
            bar_ts=bar.get("ts"),
        )

    # ——— 只读数据 ———

    def get_bar(self, field: str = "close", default: float = 0.0) -> float:
        """取当前 bar 的字段值。字段: close/open/high/low/volume/ts"""
        return self._bar.get(field, default)

    def get_history(self, n: int = 20) -> list[float]:
        """取最近 n 根 bar 的 close 价格列表（不含当前 bar）。"""
        closes = [h.get("close", 0) for h in self._history[-n:]] if self._history else []
        return closes

    def get_full_history(self, n: int = 20) -> list[dict]:
        """取最近 n 根 bar 的完整 dict 列表。"""
        return self._history[-n:] if self._history else []

    def get_param(self, key: str, default=None):
        """取策略参数（由 Web 编辑页配置）。"""
        return self._params.get(key, default)

    # ——— 下单 ———

    def buy(self, volume: float = 100, price_type: str = "LIMIT") -> Signal:
        """买入信号。仍走风控 check_order 前置。"""
        self._signal = Signal(
            action=Action.BUY, symbol=self.symbol, volume=volume,
            price=self._bar.get("close", 0), price_type=price_type,
            reason="Python 策略买入",
        )
        return self._signal

    def sell(self, volume: float = 100, price_type: str = "LIMIT") -> Signal:
        """卖出信号。"""
        self._signal = Signal(
            action=Action.SELL, symbol=self.symbol, volume=volume,
            price=self._bar.get("close", 0), price_type=price_type,
            reason="Python 策略卖出",
        )
        return self._signal

    def hold(self, reason: str = "") -> Signal:
        """持仓不动。"""
        self._signal = Signal(action=Action.HOLD, symbol=self.symbol, reason=reason or "Python 策略持有")
        return self._signal

    # ——— 状态管理 ———

    def set_state(self, key: str, value):
        """保存运行时状态（跨 tick 持久，仅内存）。"""
        self._state[key] = value

    def get_state(self, key: str, default=None):
        """读取运行时状态。"""
        return self._state.get(key, default)

    # ——— 因子调用（双通道：Python 策略也可调注册因子） ———

    def get_factor(self, name: str, **kwargs) -> float:
        """调用注册因子，返回因子值。

        用于 Python 模式策略中调用预置或自定义因子：
            ma_dev = ctx.get_factor("ma_dev", n=20)
        """
        entry = get_factor(name)
        if entry is None:
            raise ValueError(f"未知因子: {name}")
        factor = entry["cls"]()
        factor.params = {**entry.get("params", {}), **kwargs}
        return factor.compute(self._bar_ctx)


class PythonStrategy(Strategy):
    """Python 代码模式策略（#15）。

    用户通过 Web 编辑器写 Python 代码，定义 on_bar(ctx) 函数。
    代码在受限 namespace 中 exec，只可访问 ctx 对象。
    """

    def __init__(self, config: StrategyConfig, adapter):
        super().__init__(config, adapter)
        self._user_code = config.params.get("python_code", "")
        self._ctx = StrategyContext(config.id, config.symbol)
        self._compiled = None
        if self._user_code:
            # P0-1（双盲审计 F1.2 纵深）：保存路径已校验，运行侧再拦一次——覆盖存量旧代码
            _forbidden = _check_ast_blacklist(self._user_code)
            if _forbidden:
                raise ValueError(f"策略代码安全校验失败: {_forbidden}")
            try:
                self._compiled = compile(self._user_code, "<strategy>", "exec")
            except SyntaxError as e:
                raise ValueError(f"Python 策略代码语法错误: {e}")

    def on_bar(self, bar: dict, history: list[dict] | None = None) -> Signal | None:
        """Python 模式 on_bar：exec 用户代码，调用户 on_bar(ctx)。

        安全：exec 在受限 namespace 中运行，只暴露 ctx 对象和安全的 builtins 子集。
        """
        self._ctx._update(bar, history or [], self.config.params)
        # 受限 namespace：只暴露 ctx + on_bar + 安全 builtins 子集
        safe_builtins = {
            "abs": abs, "max": max, "min": min, "sum": sum,
            "round": round, "float": float, "int": int,
            "len": len, "range": range, "list": list, "dict": dict,
            "str": str, "bool": bool, "True": True, "False": False, "None": None,
            "print": print,  # print 无害（stdout 到 journal）
        }
        namespace = {"ctx": self._ctx, "on_bar": None, "__builtins__": safe_builtins}
        if self._compiled:
            exec(self._compiled, namespace)
        user_on_bar = namespace.get("on_bar")
        if user_on_bar:
            sig = user_on_bar(self._ctx)
            # 如果用户函数返回 None，取 ctx._signal
            if sig is None:
                sig = self._ctx._signal
            if sig and sig.action != Action.HOLD:
                self.place_order(sig)
            return sig
        return None