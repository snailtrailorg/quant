"""行情网关插件层（批 63 二）——hub MD 接入从 XTP 硬编码改为按 provider 插件化。

分工（批 63 方案裁定）：
- 留 hub（provider 无关）：MinuteAggregator 聚合 / _publish 分发 / 租约·gen·心跳（M5 四键）/
  _desired_symbols 订阅真相源 / SubscriptionManager diff-replay / flush 三窗。
- 进插件（provider 特有）：连接原语 / 订阅退订原语 / tick 喂入 / 重连沿 / 会话窗与监督 / SDK 守卫。

tick 契约（EMQ 等自研网关 Phase B 须产出兼容对象，duck typing）：
1. 字段面：last_price/datetime/volume/turnover/exchange/symbol + open/high/low/pre_close/
   limit_up/limit_down + bid_price_1..5/ask_price_1..5/bid_volume_1..5/ask_volume_1..5
   （_write_latest_tick 与 MinuteAggregator 消费面；vnpy TickData 原生满足）。
2. datetime 必须 tz-aware（UTC，批 56b 口径）——_in_bar_session/聚合分桶全依赖，naive 会崩。
3. exchange 须 duck-type 到 vnpy 枚举：.value 取 "SSE"/"SZSE"（非 SHSE——parts._project_symbol
   做 SSE→SHSE 项目后缀映射，EMQ 实现者照直觉给 SHSE 会产出 "XXX.SHSE.SHSE" 错键）。
4. turnover 缺失时 parts 侧 getattr(tick,"turnover",0) 静默降级——EMQ 的 amount 须显式映射到
   turnover，否则 bar.amount 恒 0 无告警。

全部 vnpy/XTP import 惰性（模块可被无 vnpy 环境导入——测试/层序）。
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod

# 观测面等值（盲审 A P3-5）：[gw] 会话日志/退订日志的 logger 名保持 md_hub——
# journalctl 按名过滤与 system_log 落库 name 字段均不因插件化漂移
logger = logging.getLogger("md_hub")

try:
    from vnpy.trader.gateway import BaseGateway
except ImportError:   # 无 vnpy 环境可导入本模块（测试/层序；hub 启动时统一报错退出）
    BaseGateway = object

_REGISTRY: dict[str, type] = {}


class ThinGateway(BaseGateway):
    """仅事件转发；7 个抽象方法全量 stub（hub 数据面永不交易，R-HALT1 代码级保证）。

    批 63 二从 md_hub.parts 移驻本模块（层序：strategy_framework 层 2 禁 import 层 3；
    ThinGateway 本质=vnpy 网关薄壳，归 vnpy 集成层）。
    """

    def connect(self, setting: dict) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def subscribe(self, req) -> None:
        self.md_api.subscribe(req)

    def send_order(self, req) -> str:
        raise NotImplementedError("hub 数据面禁用")

    def cancel_order(self, req) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def query_account(self) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def query_position(self) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def close(self) -> None:
        pass


class MdGateway(ABC):
    """行情网关插件抽象。每供应商一个子类，构造收 hub 的 SessionCounters（监督沿清零共享）。"""

    provider: str

    event_engines: tuple = ()   # 事件引擎存活面（EngineLoop R-BR12 检查用；无事件引擎的网关空元组）

    @abstractmethod
    def connect(self, cred: dict, params: dict) -> None:
        """建连（凭证/参数来自 external_interface 行解密；会话窗策略由实现内部管理）。"""

    @abstractmethod
    def subscribe(self, symbol: str) -> None:
        """订阅原语（vt_symbol，如 600000.SHSE）。"""

    @abstractmethod
    def unsubscribe(self, symbol: str) -> None:
        """退订原语（桶 flush 由 hub 侧做——「先 flush 后退订」顺序在 hub 的 unsubscribe 回调里）。"""

    @abstractmethod
    def set_on_tick(self, cb) -> None:
        """注册 tick 回调 cb(tick)（契约见模块 docstring）。"""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """连接在位（重连沿——False→True 沿触发 hub 订阅全量重放）。"""

    @abstractmethod
    def start_ready(self) -> bool:
        """启动时是否可立即回放订阅（XTP=会话窗开；其他源自定策略）。"""

    @abstractmethod
    def poll_supervise(self, in_session: bool, trading_day) -> None:
        """会话监督 tick（主循环每步驱动；XTP=续航/反应式重登/断流告警五段）。"""

    def set_context(self, fn) -> None:
        """告警上下文（hub 设订阅数文案；默认 no-op）。"""

    # shutdown 无此成员——hub 退出走 os._exit 带码自灭（原生库拆除规避是刻意设计，
    # 盲审 P3：不留永不调用的死接口）


def register_md_gateway(cls):
    _REGISTRY[cls.provider] = cls
    return cls


def create_md_gateway(provider: str, counters) -> MdGateway:
    """按接口行 provider 取网关插件（未注册 fail-fast——消费方须捕获转 exit 78）。"""
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise ValueError(f"未注册的行情网关 provider: {provider}（需实现 MdGateway 子类并 register_md_gateway）")
    return cls(counters)


def list_md_gateway_providers() -> tuple[str, ...]:
    """已注册网关 provider 集（get_interface_row 缺省选行钉定用——盲审 P1）。"""
    return tuple(sorted(_REGISTRY))


@register_md_gateway
class XtpMdGateway(MdGateway):
    """中泰 XTP 网关（批 63 二收编——行为等值包装原 hub main.py MD 接入段全套，不重写）。

    vnpy EventEngine + ThinGateway + GuardedXtpMdApi（SDK 守卫）+ XtpMdSession（每日连接窗）+
    MdSessionSupervisor（L2 五段）+ 订阅映射（_EX/EXCHANGE_VT2XTP）。
    """

    provider = "xtp"

    def __init__(self, counters):
        from vnpy.event import EventEngine
        from vnpy.trader.event import EVENT_LOG
        from vnpy.trader.constant import Exchange
        from src.strategy_framework.md_api_guard import GuardedXtpMdApi
        from src.strategy_framework.md_session import XtpMdSession, load_xtp_window_cfg
        from src.strategy_framework.runtime.alerts import make_alert
        from src.strategy_framework.runtime.mdlink import MdSessionSupervisor

        self._ee = EventEngine()
        self._ee.start()   # 绕开 MainEngine 必须自启（构造不启动，_active=False → 线程未活）
        self._gw = ThinGateway(self._ee, "XTP")
        self._api = GuardedXtpMdApi(self._gw)   # 批1：SDK 生命周期守卫（SEGV 结构性绝迹）
        self._gw.md_api = self._api
        # L2 会话（韧性分层）+每日连接窗（P2 批 08-28）：lead/lag>0 窗开沿=续航单原语建连（relogin 直登）、
        # 窗关沿=guard.suspend（logout 保持 CREATED）；任一 0=禁用（旧行为）
        _lead, _lag = load_xtp_window_cfg()
        self._sess = XtpMdSession(self._api, lead_min=_lead, lag_min=_lag)
        self._context_fn = lambda: ""
        self._sup = MdSessionSupervisor(self._sess, counters, make_alert(), role="hub",
                                        context=lambda: self._context_fn())
        self._EX = {"SHSE": Exchange.SSE, "SZSE": Exchange.SZSE,
                    "BSE": getattr(Exchange, "BSE", Exchange.SSE)}
        self.event_engines = (self._ee,)   # EngineLoop 事件线程存活检查面（R-BR12 原语义）

        # MD 生命周期可见化（2026-08-24 僵尸会话事件）：连接/断开/重登日志走 EVENT_LOG（原 hub on_log 段
        # 原样；guard 保留——vnpy 事件线程对 handler 异常不捕获，裸抛=线程死 → EngineLoop fatal exit 1）
        from src.strategy_framework.runtime.alerts import make_alert, make_guard

        @make_guard("hub.on_log", make_alert())
        def _on_log(event):
            logger.info("[gw] %s", getattr(event.data, "msg", event.data))
        self._ee.register(EVENT_LOG, _on_log)

    def set_on_tick(self, cb) -> None:
        from vnpy.trader.event import EVENT_TICK
        self._ee.register(EVENT_TICK, lambda event: cb(event.data))

    def connect(self, cred: dict, params: dict) -> None:
        from src.strategy_framework.broker import xtp_setting_from
        setting = xtp_setting_from(cred, params)
        self._api.connect(setting["账号"], setting["密码"], int(setting["客户号"]),
                          setting["行情地址"], int(setting["行情端口"]), setting.get("行情协议", "TCP"), 3,
                          defer_login=not self._sess.window_open())   # 窗关启动只建 C 对象（CREATED），窗开沿 relogin 直登

    def subscribe(self, symbol: str) -> None:
        from vnpy.trader.object import SubscribeRequest
        raw, ex = symbol.rsplit(".", 1)
        e = self._EX.get(ex)
        if e:
            self._api.subscribe(SubscribeRequest(symbol=raw, exchange=e))

    def unsubscribe(self, symbol: str) -> None:
        """SDK 原生退订（原 hub _unsubscribe 的 SDK 段原样）。

        EXCHANGE_VT2XTP 无 BSE 键——.get() 取 None 静默跳过（与 subscribe 对称）；
        非 LOGGED_IN 态裸调 C 面有炸回调线程风险，跳过+debug（真实兜底=重连后全量重放）。
        """
        from vnpy_xtp.gateway.xtp_gateway import EXCHANGE_VT2XTP
        from src.strategy_framework.md_api_guard import SdkState
        raw, ex = symbol.rsplit(".", 1)
        e = self._EX.get(ex)
        xtp_ex = EXCHANGE_VT2XTP.get(e) if e else None
        if xtp_ex is None:
            return
        if self._api.state is not SdkState.LOGGED_IN:
            logger.debug("退订跳过 %s（MD 态 %s 非登录态，待重连后 diff 补齐）",
                         symbol, self._api.state.value)
            return
        self._api.unSubscribeMarketData(raw, 1, xtp_ex)
        logger.info("退订 %s（生命周期结束）", symbol)

    @property
    def connected(self) -> bool:
        return bool(getattr(self._api, "connect_status", True))

    def start_ready(self) -> bool:
        return self._sess.window_open()

    def poll_supervise(self, in_session: bool, trading_day) -> None:
        self._sup.tick(in_session=in_session, trading_day=trading_day)

    def set_context(self, fn) -> None:
        self._context_fn = fn
