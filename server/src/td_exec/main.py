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

CMD_KEY = "td:cmd:{account}"
ALIVE_KEY = "td:exec:{account}"


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

    def __init__(self, account_id: int):
        self.account_id = int(account_id)
        self._stop = threading.Event()
        self.worker = None          # EmergencyWorker（复用执行逻辑）
        self.dispatch_closing = threading.Event()   # close 进行中（主循环分流用）
        self._r = _redis()
        self._close_thread: threading.Thread | None = None
        self._hb = None
        self._quote_account = self.account_id
        self._row: dict = {}

    def _load_account(self) -> dict:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id, provider, enabled, quote_account_id, impl "
                "FROM trading_account WHERE id=%s", (self.account_id,)).fetchone()
        if not row:
            raise RuntimeError(f"trading_account {self.account_id} 不存在——拒绝启动")
        row = dict(zip(("id", "provider", "enabled", "quote_account_id", "impl"), row))
        if not row.get("enabled"):
            raise RuntimeError(f"trading_account {self.account_id} 未启用——拒绝启动")
        self._quote_account = int(row.get("quote_account_id") or self.account_id)
        return row

    def _build(self):
        from src.strategy_runner.main import make_event_engine
        from src.strategy_runner.td_registry import build_td_runtime
        from src.strategy_runner import trading

        provider = self._row.get("provider")
        impl = self._row.get("impl") or "plugin"
        if impl != "plugin":
            raise RuntimeError(
                f"账号 {self.account_id} impl={impl!r}：legacy TD 路径已删，须 impl=plugin")
        ee = make_event_engine()
        ee.start()
        self._ee = ee
        boot_epoch = int(time.time())
        # 账号级号段注入：client_id 固定 2（每进程单连接，账号内唯一即可）；order_prefix 带 account+epoch。
        rt = build_td_runtime(provider, ee, str(self.account_id), self.account_id, boot_epoch,
                              client_id=2, order_prefix=f"a{self.account_id}:e{boot_epoch}:")
        if rt is None:
            raise RuntimeError(f"TD builder {provider} 未注册（账号 {self.account_id}）")
        self._adapter = rt["adapter"]
        self._td_api = rt["td_api"]
        self._gw = rt.get("gw")
        self._exec_port = rt.get("exec_port")
        self._adapter.account_id = self.account_id   # write_trade_log per-account 真源
        if self._exec_port is None:
            raise RuntimeError(f"账号 {self.account_id} 无 exec_port（plugin 端口缺失）——拒绝启动")

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
            adapter=self._adapter, td_api=self._td_api, account_id=self.account_id,
            tid=str(self.account_id), closing=threading.Event())

    def _start_heartbeat(self):
        from src.strategy_framework.runtime.pulse import HeartbeatWriter
        self._hb = HeartbeatWriter(
            self._r, ALIVE_KEY.format(account=self.account_id), ttl=90,
            base=lambda: {"account_id": self.account_id,
                          "provider": self._row.get("provider") or "",
                          "quote_account_id": self._quote_account})
        self._hb.beat()

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
                        "status,account_id,bar_fingerprint) "
                        "SELECT strategy_id,symbol,action,volume,price,id,'rejected',%s,%s "
                        "FROM signal_log WHERE id=%s",
                        (self.account_id, jsonb(bf) if bf else None, sig_id))
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
            self._adapter, account_id=self.account_id, order=o,
            signal_id=cmd.get("signal_id"), bar_fingerprint=cmd.get("bar_fingerprint"),
            req_id=req_id)
        trading.write_cmd_result(req_id, {"req_id": req_id, "verb": "place-order",
                                          "phase": "done", **result})

    def _dispatch(self, cmd: dict) -> None:
        verb = cmd.get("verb") or ""
        req_id = cmd.get("req_id") or ""
        if verb == "close-all":
            self.dispatch_closing.set()
            self._close_thread = threading.Thread(
                target=self._run_close, args=(cmd,), name=f"td-close-{self.account_id}", daemon=True)
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
        self._build()
        self._start_heartbeat()
        key = CMD_KEY.format(account=self.account_id)
        logger.info("td_exec 启动 account=%s provider=%s quote=%s",
                    self.account_id, self._row.get("provider"), self._quote_account)
        last_hb = time.time()
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
            if now - last_hb >= 10:
                self._hb.beat()
                try:
                    from src.quant_common.guard import sd_notify as _sd_notify
                    _sd_notify("WATCHDOG=1")   # systemd 看门狗喂狗（无 NOTIFY_SOCKET 时静默）
                except Exception:  # noqa: S110
                    pass
                last_hb = now
        self._shutdown()

    def _shutdown(self) -> None:
        logger.info("td_exec 停机 account=%s", self.account_id)
        if self._close_thread and self._close_thread.is_alive():
            self._close_thread.join(timeout=10)
            if self._close_thread.is_alive():
                logger.warning("close 线程未在 10s 内结束（systemd 兜底强杀）")
        try:
            self._r.delete(ALIVE_KEY.format(account=self.account_id))
        except Exception:  # noqa: S110
            pass


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
    acc = os.environ.get("ACCOUNT_ID") or os.environ.get("INSTANCE_NAME") or ""
    if not acc.isdigit():
        logger.error("ACCOUNT_ID 缺失/非法: %r", acc)
        sys.exit(EX_CONFIG)
    ex = TdExec(int(acc))
    _install_signal(ex._stop)
    try:
        ex.run()
    except RuntimeError as e:
        logger.error("td_exec 启动失败（配置错误）: %s", e)
        sys.exit(EX_CONFIG)


if __name__ == "__main__":
    main()
