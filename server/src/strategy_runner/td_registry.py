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


def code_terminal(td_code: str) -> int:
    """D-M0-6：同 login_key 多 TD 按 code 字典序稳定分配终端号 2..99。"""
    from src.data_platform.iface_store import get_td_identity, td_sibling_codes
    ident = get_td_identity(td_code)
    if ident is None:
        raise RuntimeError(f"td_code={td_code} 不存在，无法分配终端号")
    siblings = td_sibling_codes(ident["login_key"])
    idx = siblings.index(td_code) if td_code in siblings else 0
    return idx % 98 + 2


def build_td_runtime(provider, ee, tid, td_code: str, boot_epoch,
                     *, client_id=None, order_prefix=None) -> dict | None:
    """单入口分发：未注册返 None（调用方走 stub 硬闸分支）。

    已注册但返 None/缺键=builder bug → raise（防静默落 stub 装死——批 63 P4 第二实例的
    前向防御）；缺键在消费点（main try 外）会 KeyError 退码 1 重启 churn，此处校验转 78。

    批188：新增账号级号段注入口 client_id/order_prefix（TD 执行进程每账号单连接、固定号）。
    传入则 builder 用之；None 时回落既有任务号段派生（live_task 路径兼容不变）。
    """
    builder = TD_BUILDERS.get(provider)
    if builder is None:
        return None
    rt = builder(ee, tid, td_code, boot_epoch, client_id=client_id, order_prefix=order_prefix)
    if rt is None:
        raise RuntimeError(f"TD builder {provider} 返回 None（契约须八键 dict）")
    missing = [k for k in _RT_KEYS if k not in rt]
    if missing:
        raise RuntimeError(f"TD builder {provider} 缺键: {missing}")
    return rt


@register_td_builder("xtp")
def _build_xtp_runtime(ee, tid, td_code: str, boot_epoch, *, client_id=None, order_prefix=None) -> dict:
    """XTP TD 运行时组装（唯一路径=providers/xtp 端口）。

    旧 ThinTdGateway + XtpTdApi + XTPAdapter 组装已于批 172d 删除；
    impl 只是迁移期形态标记，不在此做就绪门（存量 legacy 行走同一路径，否则发布回滚）。
    返回 {gw, td_api, adapter, setting, td_open, lead, lag, cfg_adapter, exec_port, session_slot}。
    row_id=account_id 显式传（build_xtp_setting 取数失败 raise，禁 .env fallback——防 A 任务串 B 账户）。
    """
    # login_key 取自接口行；impl 不参与组装（legacy 代码路径已物理删除，无回落可能）
    from src.data_platform.iface_store import get_td_identity
    ident = get_td_identity(td_code)
    _login_key = (ident or {}).get("login_key") or td_code

    from datetime import datetime as _dtnow  # 盲审 A-P0：函数级导入（模块头部无 datetime）

    from src.providers.xtp import td as _td
    from src.providers.xtp.session import is_trading_day as _itd
    from src.providers.xtp.session import load_xtp_window_cfg, xtp_session_window_open
    from src.strategy_framework.runtime.alerts import make_alert, make_guard
    from src.strategy_runner.session_slot import SessionSlotAllocator

    _cid = client_id if client_id is not None else runner_client_id(tid)
    setting = build_xtp_setting(client_id=_cid, td_code=td_code)

    # 每日连接窗·TD 侧（只 A股 XTP 套窗）：窗开建连/窗关启动不连（窗开沿由
    # hub_worker._td_reconnect 补首连）；盘后不断开（XtpTdApi 无 logout）。lead/lag 任一 0=禁用日窗。
    _lead, _lag = load_xtp_window_cfg()
    _td_open = xtp_session_window_open(_dtnow.now(), _lead, _lag, trading_day=_itd())

    slot = SessionSlotAllocator.acquire(_login_key, "xtp", "td", tid, boot_epoch, _td_open,
                                        terminal_id=code_terminal(td_code))
    _alert = make_alert()
    gw = _td.make_gateway(ee)
    td_api = _td._transport.td_api_cls()(gw)
    gw.td_api = td_api
    port = _td.XtpExecutionPort(ee, gw, td_api, slot,
                                lambda n: make_guard(n, _alert), setting)
    if _td_open:
        gw.connect(setting)
    adapter = _td._LegacyCompat(port, td_code)
    logger.info("XTP 接口 %s 走插件端口（terminal_id=%s）", td_code, slot.terminal_id)
    return {"gw": gw, "td_api": td_api, "adapter": adapter, "setting": setting,
            "td_open": _td_open, "lead": _lead, "lag": _lag, "cfg_adapter": "xtp",
            "exec_port": port, "session_slot": slot}


@register_td_builder("emt_emq")
def _build_emt_runtime(ee, tid, td_code: str, boot_epoch, *, client_id=None, order_prefix=None) -> dict:
    """EMT 极速柜台 TD 运行时（P4 收口：唯一路径=providers/emt 插件端口）。

    EmtAdapter 兼任 gw/td_api（薄壳：connect(setting)=Login；connect_status 属性——
    hub_worker 仅消费这两个面）。凭证=get_interface_row 解密（不加 EmtBroker）；
    td_host 用 _parse_host_port 拆分（md_gateway 先例）。
    终端号：会话槽用 code_terminal(td_code)（SessionSlot 域 2..99），EMT SDK 号段
    20..119 由插件内部由槽号映射（tcid=(slot.terminal_id-1)%100+20，SDK 实测域 [1-127]）——
    不在本层算偏移，否则 >99 会被 SessionSlot 号段校验拒（P4 §9.5 双域分离）。
    client_id 不用：EMT 终端号由槽派生；XTP 的 client_id 是软件 Key（provider 内务差异）。
    连接窗：复用 A 股窗（xtp_session_lead/lag_min 自此双 provider 共用——知情接受）。
    """
    from datetime import datetime as _dt

    from src.data_platform.iface_store import get_td_identity
    from src.providers import ensure_registered
    from src.providers.xtp.session import is_trading_day, load_xtp_window_cfg, xtp_session_window_open
    from src.strategy_framework.broker import get_interface_row
    from src.strategy_runner.session_slot import SessionSlotAllocator

    ensure_registered()   # import 即注册；registry 清空/重载场景幂等补齐（禁裸依赖别处先 import）
    ident = get_td_identity(td_code) or {}
    _impl = ident.get("impl") or "legacy"
    _login_key = ident.get("login_key") or td_code
    if _impl not in ("plugin", "legacy"):
        raise RuntimeError(f"EMT 接口 {td_code} impl={_impl!r} 非法（只认 plugin/legacy）")
    # 两值都走本函数（唯一组装路径）：EMT 执行端口（EmtExecutionPort）未落地，
    # 故 impl 门与 td-emt-emq 的 impl=plugin 翻转一并留到端口落地时（否则 legacy 行的
    # live_task 会直接 78，而 td_exec 又因缺 exec_port 起不来——两锁互死）。

    row = get_interface_row(code=td_code)
    cred = row.get("credentials") or {}
    params = row.get("params") or {}
    from src.strategy_framework.md_gateway import _parse_host_port
    host, port = _parse_host_port(params.get("emt_td_host", ""), default_port=19088)
    setting = {"td_host": host, "td_port": port,
               "account": str(cred.get("emt_account", "")),
               "password": str(cred.get("emt_password", ""))}

    lead, lag = load_xtp_window_cfg()
    td_open = xtp_session_window_open(_dt.now(), lead, lag, trading_day=is_trading_day())

    slot = SessionSlotAllocator.acquire(_login_key, "emt_emq", "td", tid, boot_epoch, td_open,
                                        terminal_id=code_terminal(td_code))

    from src.registry import DOMAIN_TRADING, pick
    plugin = pick(DOMAIN_TRADING, "emt_emq", set(), "plugin")   # 端口未落地：按实体取插件，不声明 execution
    _prefix = order_prefix if order_prefix is not None else f"t{tid}:e{boot_epoch}:"
    if client_id is not None:
        logger.info("EMT 终端号由会话槽派生（忽略 client_id=%s）", client_id)
    adapter = plugin.build(None, "td", slot, event_engine=ee, setting=setting, order_prefix=_prefix)
    logger.info("EMT 接口 %s 走插件端口（terminal_id=%s）", td_code, slot.terminal_id)
    # gw/td_api 均 adapter（hub_worker: connect_status 消费面；main: gw.connect 窗开沿）。
    # 不返回 exec_port：EmtAdapter 只有 connect/connect_status，不是 ports.ExecutionPort；
    # live_task 侧缺省即回落 LegacyExecutionPort(adapter)（行为等价），td_exec 侧显式
    # 拒绝启动（无端口=不可执行，等 EmtExecutionPort 落地）。给个裸 adapter 会在
    # subscribe_order/connected 处 AttributeError。
    return {"gw": adapter, "td_api": adapter, "adapter": adapter, "setting": setting,
            "td_open": td_open, "lead": lead, "lag": lag, "cfg_adapter": "emt_emq",
            "session_slot": slot}
