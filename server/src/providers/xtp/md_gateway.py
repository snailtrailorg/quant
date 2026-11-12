"""XTP 行情网关（自 strategy_framework.md_gateway 迁入，P3）。

通用注册表/抽象基类留在 strategy_framework.md_gateway；本文件只含 XTP 实现。
import 本模块即注册（provider='xtp'）；由 providers.ensure_registered() 懒触发。
"""
from __future__ import annotations

import logging

from src.strategy_framework.md_gateway import MdGateway, ThinGateway, register_md_gateway

logger = logging.getLogger("md_hub")


@register_md_gateway
class XtpMdGateway(MdGateway):
    """中泰 XTP 网关（批 63 二收编——行为等值包装原 hub main.py MD 接入段全套，不重写）。

    vnpy EventEngine + ThinGateway + GuardedXtpMdApi（SDK 守卫）+ XtpMdSession（每日连接窗）+
    MdSessionSupervisor（L2 五段）+ 订阅映射（_EX/EXCHANGE_VT2XTP）。
    """

    provider = "xtp"

    def __init__(self, counters):
        from vnpy.event import EventEngine
        from vnpy.trader.constant import Exchange
        from vnpy.trader.event import EVENT_LOG

        from src.providers.xtp.md_api_guard import GuardedXtpMdApi
        from src.providers.xtp.session import XtpMdSession, load_xtp_window_cfg
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

        from src.providers.xtp.md_api_guard import SdkState
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
