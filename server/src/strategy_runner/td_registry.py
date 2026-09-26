"""D26-A：TD provider 插件注册表（批 65a 下沉自 main.py:125-188）。

立法（D5+D26 §零底座同质化）：加 provider=只加 builder 条目，禁 if provider== 硬编码
（M3 守门）。批 63 P4 EMT TD=本注册表第二实例（零动 main.py）。

单文件自足约定（B-P2-2）：新 builder 必须注册于本模块内（import 即注册；独立文件
import 本表注册=空表静默）。本模块不 sys.exit——builder 异常上抛，EX_CONFIG 处理
留在 main 调用点（78=RestartPrevent 豁免防重启风暴语义）。
"""
import logging
from typing import Callable

from src.strategy_framework.broker import build_xtp_setting, runner_client_id

logger = logging.getLogger("strategy_runner.td_registry")

TD_BUILDERS: dict[str, Callable] = {}


def register_td_builder(provider: str):
    """decorator 注册（重复注册=启动期 RuntimeError 防静默覆盖——不用 assert：-O 下被剥除）。"""
    def _wrap(fn):
        if provider in TD_BUILDERS:
            raise RuntimeError(f"TD builder 重复注册: {provider}")
        TD_BUILDERS[provider] = fn
        return fn
    return _wrap


_RT_KEYS = ("gw", "td_api", "adapter", "setting", "td_open", "lead", "lag", "cfg_adapter")


def build_td_runtime(provider, ee, tid, account_id, boot_epoch) -> dict | None:
    """单入口分发：未注册返 None（调用方走 stub 硬闸分支）。

    已注册但返 None/缺键=builder bug → raise（防静默落 stub 装死——批 63 P4 第二实例的
    前向防御）；缺键在消费点（main try 外）会 KeyError 退码 1 重启 churn，此处校验转 78。
    """
    builder = TD_BUILDERS.get(provider)
    if builder is None:
        return None
    rt = builder(ee, tid, account_id, boot_epoch)
    if rt is None:
        raise RuntimeError(f"TD builder {provider} 返回 None（契约须八键 dict）")
    missing = [k for k in _RT_KEYS if k not in rt]
    if missing:
        raise RuntimeError(f"TD builder {provider} 缺键: {missing}")
    return rt


@register_td_builder("xtp")
def _build_xtp_runtime(ee, tid, account_id, boot_epoch) -> dict:
    """D5：XTP 专属 TD 运行时组装（ThinTdGateway + XtpTdApi + XTPAdapter + 连接窗）。

    （批 65a 迁自 main.py:125-184，适配三处：import 源/logger/模块归属。）
    返回 {gw, td_api, adapter, setting, td_open, lead, lag, cfg_adapter}。
    row_id=account_id 显式传（build_xtp_setting 取数失败 raise，禁 .env fallback——防 A 任务串 B 账户）。
    """
    from vnpy.trader.gateway import BaseGateway
    from vnpy_xtp.gateway.xtp_gateway import XtpTdApi
    from src.strategy_framework.adapters import XTPAdapter
    setting = build_xtp_setting(client_id=runner_client_id(tid), row_id=account_id)

    # 每日连接窗·TD 侧（只 A股 XTP 套窗）：窗开建连/窗关启动不连（窗开沿由
    # hub_worker._td_reconnect 补首连）；盘后不断开（XtpTdApi 无 logout）。lead/lag 任一 0=禁用日窗。
    from datetime import datetime as _dtnow   # 盲审 A-P0：函数级导入（模块头部无 datetime）
    from src.strategy_framework.md_session import is_trading_day as _itd
    from src.strategy_framework.md_session import load_xtp_window_cfg, xtp_session_window_open
    _lead, _lag = load_xtp_window_cfg()
    _td_open = xtp_session_window_open(_dtnow.now(), _lead, _lag, trading_day=_itd())

    class ThinTdGateway(BaseGateway):
        """TD-only 壳：事件转发 + 抽象方法转发 td_api（零 MD 零合约表，R-BR1/R-CAP1）。"""

        def connect(self, s: dict) -> None:
            self.td_api.connect(s["账号"], s["密码"], int(s["客户号"]), s["交易地址"],
                                int(s["交易端口"]), s.get("授权码", ""), 3)

        def subscribe(self, req) -> None:  # hub 模式 worker 无行情
            pass

        def send_order(self, req) -> str:
            return self.td_api.send_order(req)

        def cancel_order(self, req) -> None:
            self.td_api.cancel_order(req)

        def query_account(self) -> None:
            self.td_api.query_account()

        def query_position(self) -> None:
            self.td_api.query_position()

        def close(self) -> None:
            try:
                if getattr(self.td_api, "connect_status", False):
                    self.td_api.exit()
            except Exception:
                pass

    gw = ThinTdGateway(ee, "XTP")
    td_api = XtpTdApi(gw)
    gw.td_api = td_api
    if _td_open:
        gw.connect(setting)   # 只连 TD（R-TD1：hub 零 TD，worker 零 MD）
    else:
        logger.info("TD 窗关启动（lead=%d/lag=%d），连接待窗开沿", _lead, _lag)

    adapter = XTPAdapter(gateway=gw, event_engine=ee,
                         order_prefix=f"t{tid}:e{boot_epoch}:")
    return {"gw": gw, "td_api": td_api, "adapter": adapter, "setting": setting,
            "td_open": _td_open, "lead": _lead, "lag": _lag, "cfg_adapter": "xtp"}
