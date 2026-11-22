"""批188 · TD 执行进程（每交易账号一个常驻进程，承接全部交易操作）。

为什么存在：批187 上产后，无 running live_task 时点清仓报 409——TD 会话绑在任务进程，
任务不跑就没有执行载体。本进程把 TD 会话常驻化：systemd 保活、SA4 期望态恒在，下发零判活，
进程不活即系统级故障（systemd 自愈 + health_monitor 告警）。

执行模型（饥饿根治）：
- 命令通道 = Redis List "td:cmd:{account}"（LPUSH 写、BLPOP 读；并发命令不覆盖）。
- 主循环 BLPOP 取命令 → 分流：
  - close-all：起专用线程跑（独占 adapter），置 dispatch_closing；线程结束清；
  - close 进行中 + 其它 verb：主循环直接写 reject 回执（不投线程、不排队、不超时）；
  - 其它情形：主循环同步执行（place/cancel/manual 毫秒~秒级）。
- close/cancel/manual 的执行逻辑整体复用 strategy_runner.hub_worker.EmergencyWorker
  （_handle 分发 + _close_all/_cancel_all/_manual_order + _final/SSE），不重写已验证的急停链；
  仅不再用它自带的 queue/daemon（由本模块主循环与 close 线程驱动）。
- 回执/进度/终局/取消键族沿用批187 "risk:cmd:*" 原名，三处读端与前端零改动。

取消：web 取消按钮直接写 "risk:cmd:cancel:{req_id}"（不经队列），_close_all_positions 每轮检查，
故 close 可被取消且不依赖工作线程让出。

取价：清仓按本账号 trading_account.quote_account_id 查行情账号，读 "hub:latest_tick:{quote}:{vt}"。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time

logger = logging.getLogger("td_exec")

EX_CONFIG = 78  # 永久配置错误 → RestartPreventExitStatus=78（对齐既有单元语义）
EX_TEMPFAIL = 75  # 会话被占/DB 不可用 → 退避重试（同 strategy_runner 语义）

CMD_KEY = "td:cmd:{account}"
ALIVE_KEY = "iface:hb:td:{account}"


def _redis(decode: bool = True):
    from src.quant_common.redis_client import business_redis
    return business_redis(decode_responses=decode, socket_timeout=3)


def _parse_cmd(raw) -> dict | None:
    """List 元素 → 规范命令 dict（对齐 hub_worker.consume_risk_cmd 的 item 结构）。"""
    try:
        cmd = json.loads(raw) if isinstance(raw, (str, bytes)) else (raw or {})
    except (ValueError, TypeError):
        return None
    if not isinstance(cmd, dict):
        return None
    return {
        "req_id": cmd.get("id") or cmd.get("req_id") or "",
        "verb": cmd.get("verb") or "",
        "operator": cmd.get("operator") or "manual",
        "operator_uid": cmd.get("operator_uid"),
        "ts": cmd.get("ts"),
        "order": cmd.get("order") or {},
        "signal_id": cmd.get("signal_id"),
        "bar_fingerprint": cmd.get("bar_fingerprint"),
        "skipped_accounts": cmd.get("skipped_accounts") or [],
    }


class TdExec:
    """账号级 TD 执行进程主体。"""

    def __init__(self, td_code: str):
        self.td_code = td_code
        self._stop = threading.Event()
        self.worker = None          # EmergencyWorker（复用执行逻辑）
        self.dispatch_closing = threading.Event()   # close 进行中（主循环分流用）
        self._r = _redis()
        self._close_thread: threading.Thread | None = None
        self._hb = None
        self._quote_md_code = td_code   # 取价主 MD code（M5：td_md_map 反查，见 _load_account）
        self._row: dict = {}
        self._td_window: tuple[int, int] | None = None   # XTP 每日连接窗（lead, lag）
        self._enabled = True                             # 配置启用态（I4：只决定干活/空转）
        self._slot = None                                # 会话租约（builder 产出；本进程负责续租/释放）
        self._exit_code = 0                              # 停机退出码（租约被抢=75）
        self._hb_seq = 0                                 # C4：心跳序号（单调递增）
        self._last_conn_ts = 0.0                         # 窗开沿建连 60s 节流
        self._conn_announced = False                     # 连上沿已对账（XTP 登录异步，靠轮询沿）
        self._last_flush = 0.0                           # 账户/持仓快照 60s 刷新节流
        self._baseline_cache: dict = {}                  # _account_baseline_capital 缓存（每进程一份）

    def _load_account(self) -> dict:
        from src.data_platform.iface_store import get_td_identity, list_mappings
        ident = get_td_identity(self.td_code)
        if not ident:
            raise RuntimeError(f"trade_interface {self.td_code} 不存在——拒绝启动")
        # I4：enabled 只管干活/空转——停用接口仍常驻（不建连、不下单、下发按 IFACE_IDLE 拒）
        self._enabled = bool(ident.get("enabled"))
        if not self._enabled:
            logger.info("接口 %s 已停用：进程常驻空转（不建连/不下单）", self.td_code)
        # I6/E1：取价主 MD code 由 td_md_map 派生（quote_account_id 已退役）
        maps = list_mappings("td", self.td_code)
        self._quote_md_code = maps[0]["md_code"] if maps else ""
        self._row = ident
        if ident.get("provider") == "xtp":
            # 与 live_task 同源（hub_worker._td_connect_due）：启动时读一次，改动重启生效
            from src.providers.xtp.session import load_xtp_window_cfg
            self._td_window = load_xtp_window_cfg()
        return ident

    def _build(self):
        from src.strategy_runner.main import make_event_engine
        from src.strategy_runner.td_registry import build_td_runtime
        from src.strategy_runner import trading

        from src.strategy_runner.td_registry import code_terminal
        provider = self._row.get("provider")
        # impl 只是迁移期形态标记，不是就绪条件（I4）：载体由 builder/pick 决定，
        # 存量 impl=legacy 的行也走唯一插件路径，不得在此 78 把发布波次拖进回滚。
        ee = make_event_engine()
        ee.start()
        self._ee = ee
        boot_epoch = int(time.time())
        # 接口级号段：client_id 固定 2；order_prefix 带 code+epoch；tid 用 code 字典序稳定整数。
        _tid = code_terminal(self.td_code)
        rt = build_td_runtime(provider, ee, _tid, self.td_code, boot_epoch,
                              client_id=2, order_prefix=f"a{self.td_code}:e{boot_epoch}:")
        if rt is None:
            raise RuntimeError(f"TD builder {provider} 未注册（接口 {self.td_code}）")
        self._adapter = rt["adapter"]
        self._td_api = rt["td_api"]
        self._gw = rt.get("gw")
        self._exec_port = rt.get("exec_port")
        self._slot = rt.get("session_slot")       # 常驻进程须自续租（不续=45s 后号段可被并发抢占）
        self._adapter.account_id = self.td_code   # write_trade_log 归属真源（现为 code）
        if self._exec_port is None:
            raise RuntimeError(f"接口 {self.td_code} 无 exec_port（plugin 端口缺失）——拒绝启动")

        def on_order(d):
            _err = ""
            try:
                from src.quant_common.domain import OrderStatus
                if getattr(d, "status", None) == OrderStatus.REJECTED:
                    fn = getattr(self._exec_port, "pop_order_error", None)
                    if callable(fn):
                        _err = fn() or ""
            except Exception:  # noqa: BLE001
                _err = ""
            trading.write_order_status(d, self._adapter, "", "", error=_err)
            try:
                from src.quant_common.eventbus import bus
                bus.publish_cross_process(0, "order_update", {})
            except Exception:  # noqa: S110
                pass

        def on_trade(t):
            trading.write_trade_log(t, self._adapter, "", "")
            try:
                from src.quant_common.eventbus import bus
                bus.publish_cross_process(0, "trade_update", {})
            except Exception:  # noqa: S110
                pass

        self._exec_port.subscribe_order(on_order)
        self._exec_port.subscribe_trade(on_trade)

        # 启动/重连对账（F-4 宕机窗补录）
        try:
            trading.reconcile_orders(self._adapter, "", "")
        except Exception as e:  # noqa: BLE001
            logger.warning("启动对账失败（不阻断启动）: %s", e)

        # 复用 EmergencyWorker 执行逻辑（queue/daemon 不用，由本模块驱动 _handle）
        from src.strategy_runner.hub_worker import EmergencyWorker
        self.worker = EmergencyWorker(
            adapter=self._adapter, td_api=self._td_api, account_id=self.td_code,
            tid=str(code_terminal(self.td_code)), closing=threading.Event(),
            quote_md_code=self._quote_md_code)

    def _maybe_connect(self) -> None:
        """窗开沿建连 + 连上沿对账（与 live_task 同一纯函数语义：交易日门 + 60s 节流）。

        常驻进程常在盘外启动（批量上产/凌晨 systemd 重启）——_build 时窗关 connect() 跳过，
        没有这一腿当日开盘就没有 TD 会话，下单全拒且无进程级报错。
        XTP 登录是**异步**的（connect() 返回时 connect_status 仍 False），故连上沿观测心跳式轮询：
        每 tick 看 connected，False→True 的沿做一次对账（补宕机窗成交），并只在沿上打日志。
        """
        if not getattr(self, "_enabled", True):
            return
        if getattr(self, "_exec_port", None) is None:
            return
        win = self._td_window
        if not win or self._exec_port is None:
            return
        try:
            connected = bool(self._exec_port.connected)
        except Exception:  # noqa: BLE001 —— 端口无 connected 属性按未连处理
            return
        if connected:
            if not self._conn_announced:
                self._conn_announced = True
                logger.info("TD 会话就绪 td_code=%s", self.td_code)
                try:
                    from src.strategy_runner import trading
                    trading.reconcile_orders(self._adapter, "", "")
                except Exception as e:  # noqa: BLE001
                    logger.warning("连上沿对账失败: %s", e)
            return
        self._conn_announced = False
        now = time.time()
        if now - self._last_conn_ts < 60.0:
            return
        from datetime import datetime
        from src.providers.xtp.session import is_trading_day, xtp_session_window_open
        if not xtp_session_window_open(datetime.now(), win[0], win[1],
                                       trading_day=is_trading_day()):
            return
        self._last_conn_ts = now
        try:
            self._exec_port.connect(force=True)   # 实时窗口已由上一行判定；槽内快照是建槽时刻的陈旧值
        except Exception as e:  # noqa: BLE001
            logger.warning("TD 窗开建连失败（60s 后重试）: %s", e)

    def _config_digest(self):
        """接口行身份/凭证/启用态 + 取价映射摘要（变更=带码重启生效；映射热改不重启会半生效）。"""
        from src.data_platform.iface_store import get_td_identity, list_mappings
        ident = get_td_identity(self.td_code) or {}
        maps = tuple((m["md_code"], m["priority"]) for m in list_mappings("td", self.td_code))
        return (ident.get("provider"), ident.get("credentials_encrypted"),
                ident.get("params"), bool(ident.get("enabled")), maps)

    def _reload_iface(self) -> None:
        if self._config_digest() != getattr(self, "_cfg_digest", None):
            logger.info("接口行配置变更（provider/凭证/启用态）——退出码 9 重启生效")
            os._exit(9)

    def _renew_slot(self) -> None:
        """会话租约续租（10s 节拍，TTL 45s）。被抢=立即停机：双会话比停机危险。"""
        if self._slot is None:
            return
        from src.strategy_runner.session_slot import SessionLost, SessionSlotAllocator, SessionUnavailable
        try:
            SessionSlotAllocator.renew(self._slot)
        except SessionLost:
            logger.error("会话号被抢占（terminal_id=%s）——立即停机避免双会话",
                         self._slot.terminal_id)
            self._exit_code = 75
            self._stop.set()
        except SessionUnavailable as e:  # noqa: BLE001 —— DB 抖动：下轮重试
            logger.warning("会话租约续租失败（下轮重试）: %s", e)

    def _maybe_flush_positions(self) -> None:
        """60s 账户+持仓快照刷新（live_task 退役后由本进程承接；复用 trading.snapshot_cycle）。

        缺这一腿的后果（prod 实录）：无 live_task 时 account_snapshot / position_snapshot 永久停在
        最后一次任务那天——Web 持仓页是旧仓，风控「账户快照陈旧」fail-closed 把所有平仓单拒掉
        （清仓无限重试但永远发不出去）。只写不判：失败仅 warning，不阻断主循环。
        """
        now = time.time()
        if now - self._last_flush < 60.0:
            return
        try:
            if not self._exec_port.connected:
                return
        except Exception:  # noqa: BLE001
            return
        self._last_flush = now
        try:
            from src.strategy_runner import trading
            trading.snapshot_cycle(self._adapter, self.td_code, None, self._baseline_cache)
        except Exception as e:  # noqa: BLE001
            logger.warning("账户/持仓快照刷新失败: %s", e)

    def _start_heartbeat(self):
        from src.strategy_framework.runtime.pulse import HeartbeatWriter
        self._hb = HeartbeatWriter(
            self._r, ALIVE_KEY.format(account=self.td_code), ttl=90,
            base=lambda: {"account_code": self.td_code,
                          "provider": self._row.get("provider") or "",
                          "market": self._row.get("market") or "",
                          "quote_md_code": self._quote_md_code})
        self._hb_seq += 1
        self._hb.beat(seq=self._hb_seq, enabled=int(self._enabled), working=0,
                      connected=0, conn=0)

    def _reject(self, cmd: dict) -> None:
        """close 进行中：主循环直接写 reject 回执（不投线程、不排队）。"""
        from src.strategy_runner import trading
        req_id = cmd.get("req_id") or ""
        reason = "系统处置中，请稍后或先取消当前处置"
        sig_id = cmd.get("signal_id")
        if sig_id:
            try:
                from src.data_platform.db import get_conn
                from src.data_platform.jsonb import jsonb
                bf = cmd.get("bar_fingerprint")
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO order_log (strategy_id,symbol,action,volume,price,signal_id,"
                        "status,account_code,bar_fingerprint) "
                        "SELECT strategy_id,symbol,action,volume,price,id,'rejected',%s,%s "
                        "FROM signal_log WHERE id=%s",
                        (self.td_code, jsonb(bf) if bf else None, sig_id))
                    conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning("reject 信号账回写失败 sig=%s: %s", sig_id, e)
        trading.write_cmd_result(req_id, {
            "req_id": req_id, "verb": cmd.get("verb") or "", "phase": "done",
            "accepted": False, "reason": reason})

    def _run_close(self, cmd: dict) -> None:
        try:
            self.worker._handle(cmd)     # 内部分发 close-all → _close_all + _final(SSE)
        except Exception:
            logger.exception("close 线程异常 req=%s", cmd.get("req_id"))
        finally:
            self.dispatch_closing.clear()

    def _place_order(self, cmd: dict) -> None:
        """形态X 共同路径下单（check_order→WAL→合规→send_order→回执）。"""
        from src.strategy_runner import trading
        req_id = cmd.get("req_id") or ""
        o = cmd.get("order") or {}
        result = trading.execute_place_order(
            self._adapter, account_id=self.td_code, order=o,
            signal_id=cmd.get("signal_id"), bar_fingerprint=cmd.get("bar_fingerprint"),
            req_id=req_id)
        trading.write_cmd_result(req_id, {"req_id": req_id, "verb": "place-order",
                                          "phase": "done", **result})

    def _dispatch(self, cmd: dict) -> None:
        verb = cmd.get("verb") or ""
        req_id = cmd.get("req_id") or ""
        if not getattr(self, "_enabled", True):
            from src.strategy_runner import trading
            trading.write_cmd_result(req_id, {
                "req_id": req_id, "verb": verb, "phase": "done", "accepted": False,
                "code": "IFACE_IDLE", "reason": "接口已停用（空闲）"})
            return
        if verb == "close-all":
            self.dispatch_closing.set()
            self._close_thread = threading.Thread(
                target=self._run_close, args=(cmd,), name=f"td-close-{self.td_code}", daemon=True)
            self._close_thread.start()
        elif self.dispatch_closing.is_set():
            self._reject(cmd)
        elif verb == "place-order":
            try:
                self._place_order(cmd)
            except Exception:
                logger.exception("place-order 执行异常 req=%s", req_id)
        else:
            try:
                self.worker._handle(cmd)
            except Exception:
                logger.exception("命令执行异常 req=%s verb=%s", req_id, verb)

    def run(self) -> None:
        self._row = self._load_account()
        if self._enabled:
            self._build()
        else:
            # 停用/草稿行：不组装运行时（无凭证也能常驻空转，不建连、不下单）
            logger.info("td_exec 空转启动 td_code=%s（接口停用）", self.td_code)
        self._start_heartbeat()
        key = CMD_KEY.format(account=self.td_code)
        logger.info("td_exec 启动 td_code=%s provider=%s quote_md=%s",
                    self.td_code, self._row.get("provider"), self._quote_md_code)
        last_hb = time.time()
        last_cfg = time.time()
        self._cfg_digest = self._config_digest()
        while not self._stop.is_set():
            try:
                item = self._r.blpop(key, timeout=1)
            except Exception as e:  # noqa: BLE001
                logger.warning("BLPOP 失败（1s 后退避）: %s", e)
                item = None
                time.sleep(1)
            if item:
                raw = item[1] if isinstance(item, (list, tuple)) else item
                cmd = _parse_cmd(raw)
                if cmd:
                    self._dispatch(cmd)
                else:
                    logger.warning("命令解析失败（丢弃）: %r", item)
            now = time.time()
            if now - last_cfg >= 15:
                self._reload_iface()
                last_cfg = now
            if now - last_hb >= 10:
                self._renew_slot()
                self._maybe_connect()
                self._maybe_flush_positions()
                try:
                    _conn = bool(self._exec_port.connected)
                except Exception:  # noqa: BLE001
                    _conn = False
                self._hb_seq += 1
                self._hb.beat(seq=self._hb_seq,
                              working=int(getattr(self, "_enabled", True) and _conn),
                              connected=int(_conn), conn=int(_conn),
                              enabled=int(getattr(self, "_enabled", True)))
                try:
                    from src.quant_common.guard import sd_notify as _sd_notify
                    _sd_notify("WATCHDOG=1")   # systemd 看门狗喂狗（无 NOTIFY_SOCKET 时静默）
                except Exception:  # noqa: S110
                    pass
                last_hb = now
        self._shutdown()

    def _shutdown(self) -> None:
        logger.info("td_exec 停机 td_code=%s", self.td_code)
        if self._close_thread and self._close_thread.is_alive():
            self._close_thread.join(timeout=10)
            if self._close_thread.is_alive():
                logger.warning("close 线程未在 10s 内结束（systemd 兜底强杀）")
        _slot = getattr(self, "_slot", None)
        if _slot is not None:
            from src.strategy_runner.session_slot import SessionSlotAllocator
            SessionSlotAllocator.release(_slot)   # 带 holder 条件，不会删抢到者的行
        try:
            self._r.delete(ALIVE_KEY.format(account=self.td_code))
        except Exception:  # noqa: S110
            pass
        # 硬退出（md_hub 同款）：vnpy EventEngine 线程非 daemon、XTP SDK 自带线程，
        # 仅 return 不退出 → systemd 等满 TimeoutStopSec(90s) SIGKILL → Result=timeout
        # → OnFailure 误告警。清理已在上方完成，此处直接带码退出。
        try:
            _ee = getattr(self, "_ee", None)
            if _ee is not None:
                _ee.stop()
        except Exception:  # noqa: S110
            pass
        logging.shutdown()
        os._exit(getattr(self, "_exit_code", 0))


def _install_signal(stop: threading.Event) -> None:
    """SIGTERM/SIGINT → 置 stop（主循环自然退出）。接管后必须自退出：不吞信号。"""
    def _h(signum, frame):
        logger.info("收到信号 %s，请求停机", signum)
        stop.set()
    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _h)
        except (ValueError, OSError):
            pass


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    code = os.environ.get("TD_CODE") or ""
    # 防复活守门：legacy @quant concrete unit 无 TD_CODE（或 ACCOUNT_ID=quant）→ exit 78 Prevent。
    # code 形如 td-xtp；quant 无 td- 前缀自然拒绝。
    import re as _re
    if not _re.match(r"^td-[a-z0-9-]+$", code or ""):
        logger.error("TD_CODE 缺失/非法: %r（须 ^td-[a-z0-9-]+$）", code)
        sys.exit(EX_CONFIG)
    ex = TdExec(code)
    _install_signal(ex._stop)
    from src.strategy_runner.session_slot import (
        SessionBusy, SessionConfigError, SessionUnavailable,
    )
    try:
        ex.run()
    except RuntimeError as e:
        logger.error("td_exec 启动失败（配置错误）: %s", e)
        sys.exit(EX_CONFIG)
    except (SessionBusy, SessionUnavailable) as e:
        # 瞬时（号段被占/DB 抖）→ 75 让 systemd 退避重拉；按 1（非约定码）退出会误触 StartLimit
        logger.error("td_exec 会话不可用（退避重试）: %s", e)
        sys.exit(EX_TEMPFAIL)
    except SessionConfigError as e:
        logger.error("td_exec 会话配置错误（不重试）: %s", e)
        sys.exit(EX_CONFIG)


if __name__ == "__main__":
    main()
