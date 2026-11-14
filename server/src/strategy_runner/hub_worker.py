"""hub 模式 worker（ST7，设计 14 v2 §3）。批 4b（2026-08-27）迁 runtime 骨架。

由 strategy_runner.main 在 md_mode=hub 时调用 run()：TD-only 接入 + Valkey Streams 消费
+ SA/SB/SC 机制全复用。交易四件套与 frozen/buy_ok 自 4a 单源化于 strategy_runner.trading；
批 4b：5s 定时段逐项退化为 EngineLoop.every() 钩子（XReadSleeper 双节奏注入）；停止路径=
_stop_hook 清理+os._exit(0)（不用 failure=exit）；心跳只写自有 7 字段+ts（D3）；三件套收编
runtime.alerts。钩子全清单 11 项与知情差异见 docs/obsolete/任务归档/批4-worker迁移与trading解耦.md。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone

from src.strategy_framework.runtime.alerts import make_alert, make_guard, make_valkey
from src.strategy_framework.runtime.loop import EngineLoop
from src.strategy_framework.runtime.pulse import HeartbeatWriter
from src.strategy_framework.runtime.xsleeper import XReadSleeper
from src.strategy_runner import trading
from src.data_platform.log_sink import exit_process

logger = logging.getLogger("hub_worker")

BAR_STREAM_PREFIX = "hub:bars:"   # 前缀常量（终态键 hub:bars:{account_id}:{symbol}——bar_stream_key 单点构造）
HB_KEY = "quant:hb:md-hub"        # 前缀常量（per-account 键 quant:hb:md-hub:{account_id}——_hub_alive 单点构造）
STALE_PUB_S = 60             # pub_ts 超龄丢弃（R-DL3）


def _crypto_provider(symbol: str) -> str | None:
    """加密 symbol 后缀 → provider（DataBus 暖机 source 路由用——multi-source 架构的
    provider 任务级路由，非键域判定）。批 66b：键判定退役 account_id 单一谓词后，
    本函数仅供 strategy_runner.main._warmup_history 的 DataRequest source 过滤。"""
    if symbol.endswith((".XBIN", ".BINANCE")):
        return "binance_perp"
    if symbol.endswith((".XOKX", ".OKX")):
        return "okx_perp"
    return None


# 批 171s：急停命令通道（Web 端点 → GETDEL 请求键 → 主循环拍**只投递** → 专用线程执行）。
# 键：请求 risk:cmd:{tid}（值 JSON {id, verb, operator, operator_uid, ts}，TTL 10min——
# GETDEL 天然幂等单消费）；结果键格式单点在 trading.RISK_RESULT_KEY / RISK_FINAL_KEY。
RISK_CMD_KEY = "risk:cmd:{tid}"
RISK_CMD_RESULT_KEY = trading.RISK_RESULT_KEY   # 别名：格式真源在 trading（回执写口）


def consume_risk_cmd(r, adapter, tid: str, account_id=None, td_api=None,
                     cmd_queue=None) -> dict | None:
    """批 171s 件四：主循环拍内**只投递不执行**（模块级可直测——56b 闭包不可测教训）。

    GETDEL 取命令 → 解析 req_id/verb/operator/operator_uid → 投给该 runner 的
    EmergencyWorker daemon 线程队列 → **立即返回**（不 sleep、不循环、不发单、不碰
    adapter 下单/平仓）。TD 断线防线（171o A-P0-2）随执行一起搬入 EmergencyWorker。
    cmd_queue 为 None（旧直测形态）时只解析不投递，warning 留痕不静默丢命令。
    adapter / account_id / td_api 保留形参以兼容既有调用点，本函数不再使用。
    """
    raw = None
    try:
        raw = r.getdel(RISK_CMD_KEY.format(tid=tid))
    except AttributeError:            # 老驱动回退（非原子——同 _unfreeze_poll 形态）
        try:
            raw = r.get(RISK_CMD_KEY.format(tid=tid))
            if raw:
                r.delete(RISK_CMD_KEY.format(tid=tid))
        except Exception:
            raw = None
    except Exception:
        return None
    if not raw:
        return None
    try:
        cmd = json.loads(raw) if isinstance(raw, str) else {}
    except (ValueError, TypeError):
        cmd = {}
    item = {
        "req_id": cmd.get("id") or "",
        "verb": cmd.get("verb") or "",
        "operator": cmd.get("operator") or "manual",
        "operator_uid": cmd.get("operator_uid"),   # worker3 _dispatch_risk_cmd 补 int(sub)
        "ts": cmd.get("ts"),
        "order": cmd.get("order") or {},   # 批173：手动买卖参数（verb=manual-order）
    }
    if cmd_queue is not None:
        cmd_queue.put_nowait(item)
    else:
        logger.warning("急停命令未投递（cmd_queue 缺失）tid=%s req=%s verb=%s",
                       tid, item["req_id"], item["verb"])
    return {"id": item["req_id"], "verb": item["verb"],
            "dispatched": cmd_queue is not None}


def bar_stream_key(symbol: str, account_id: int) -> str:
    """D26 批 66b：全市场统一 per-account 流键 `hub:bars:{account_id}:{symbol}`。

    account_id 必填（上游 --task-id 路径由 live_task.account_id NOT NULL 保证+main 层
    get_interface_row 必填 raise 双兜底）；None/非法 raise=第二道防线（防裸键复活）。
    """
    if account_id is None:
        raise ValueError("account_id required——裸键形态已退役（批 66b，D26 账号级 hub）")
    return f"hub:bars:{account_id}:{symbol}"


def _account_mismatch(fields: dict, account_id) -> bool:
    """D26 批 66b 同源校验（无市场跳过分支）：流消息 account_id 与本任务不一致=True
    （跨源/缺失/非法，fail-closed 拒——防吃错行情下错单）。payload 契约保证全市场
    消息恒带 account_id（hub msg_of 无条件写）；account_id=None（异常态）→ True。"""
    if account_id is None:
        return True
    try:
        return int(fields.get("account_id", -1)) != account_id
    except (TypeError, ValueError):
        return True
# 批 2 骨架三件套（与 hub 同款；_valkey 保留别名供测试/冒烟注入 fake）
_alert = make_alert()
_valkey = make_valkey


_STOP_CMD = object()   # EmergencyWorker 停机哨兵（drain 投递，唤醒空闲线程）


class EmergencyWorker:
    """批 171s 件四：每 runner 一个 daemon 线程，串行消费急停命令（queue.Queue）。

    主循环拍（_risk_cmd_poll，1s）只 GETDEL→投递→立即返回；整轮急停（撤→平稳定窗
    2.0s、拒单回报窗 1.5s、重取价重试循环、终局回执/SSE）全部在本线程内完成——
    主循环内零 sleep。_closing Event 置位期间 Gate 同时拦具名钩子链与行情消费链
    （process_batch），保证同账号全部 adapter 写归本线程、零竞态。
    """

    def __init__(self, *, adapter, td_api, account_id, tid, closing: threading.Event):
        self._q: queue.Queue = queue.Queue()
        self._adapter = adapter
        self._td_api = td_api
        self._account_id = account_id
        self._tid = tid
        self._closing = closing
        self._current_req_id = ""
        self._current_verb = ""
        self._thread = threading.Thread(target=self._run,
                                        name=f"emergency-{tid}", daemon=True)

    @property
    def cmd_queue(self) -> queue.Queue:
        return self._q

    @property
    def current_req_id(self) -> str:
        return self._current_req_id

    @property
    def current_verb(self) -> str:
        return self._current_verb

    def start(self) -> None:
        self._thread.start()

    def drain(self, timeout_s: float) -> bool:
        """限时停机：投哨兵 + join；返回是否已退出（False=整轮在飞，调用方写 interrupted）。"""
        self._q.put_nowait(_STOP_CMD)
        self._thread.join(timeout=max(0.1, float(timeout_s)))
        return not self._thread.is_alive()

    def _run(self) -> None:
        while True:
            cmd = self._q.get()
            try:
                if cmd is _STOP_CMD:
                    return
                try:
                    self._handle(cmd)
                except Exception:
                    # never-raise 但 never-silent：异常也写终局 + 告警 + logger.exception
                    logger.exception("急停线程命令异常 req=%s verb=%s",
                                     cmd.get("req_id"), cmd.get("verb"))
                    _alert(f"急停线程异常（{self._tid}）",
                           f"req={cmd.get('req_id')} verb={cmd.get('verb')}；"
                           "已写 interrupted 终局，请查 journalctl。",
                           code="emergency.worker_error")
                    self._final(cmd, {"error": "internal_error", "pending": [],
                                      "no_quote": [], "retrying": False,
                                      "closed_requested": 0, "positions_remain": -1},
                                interrupted=True)
            finally:
                self._q.task_done()

    def _td_connected(self) -> bool:
        return bool(getattr(self._td_api, "connect_status", False))

    def _cancel_all(self, cmd) -> dict:
        if not self._td_connected():
            return {"error": "td_disconnected",
                    "detail": "TD 会话断线——缓存不可信，拒绝执行；恢复连接后重试"}
        # 批 171s：撤单同样碰 adapter（cancel_order）——必须与心跳/快照/行情链互斥，
        # 复用同一 _closing Gate（短窗；close-all 分支在外层已有置位时重复 set 无害）。
        self._closing.set()
        try:
            return trading._cancel_working_orders(self._adapter)
        finally:
            self._closing.clear()

    def _close_all(self, cmd) -> dict:
        # 批 171o A-P0-2：TD 断线时 query_position 清缓存返 []=假「已清仓」、
        # query_orders=残影；执行比拒绝更危险 ⇒ 回执 td_disconnected 不执行。
        if not self._td_connected():
            return {"error": "td_disconnected",
                    "detail": "TD 会话断线——缓存不可信（持仓清空=假清仓/委托=残影），"
                              "拒绝执行；恢复连接后重试"}
        trading._cancel_working_orders(self._adapter)   # 撤在场单
        time.sleep(2.0)   # 撤→平稳定窗（frozen 释放；专用线程内，主循环零 sleep）
        try:
            from src.data_platform.hub_quote import get_retry_config
            deadline_s = float(get_retry_config().get("deadline_s") or 120)
        except Exception:
            deadline_s = 120.0
        deadline_ts = time.time() + deadline_s
        return trading._close_all_positions(
            self._adapter, account_id=self._account_id,
            operator=cmd.get("operator") or "manual",
            req_id=cmd.get("req_id") or "", deadline_ts=deadline_ts,
            td_api=self._td_api)

    def _manual_order(self, cmd) -> dict:
        """批 173：手动买卖在专用线程内执行（web-api 无 TD 会话）。"""
        o = cmd.get("order") or {}
        if not o:
            logger.warning("手动单命令缺 order（丢弃）tid=%s req=%s",
                           self._tid, cmd.get("req_id"))
            return {"accepted": False, "reason": "BAD_CMD"}
        if not self._td_connected():
            return {"accepted": False, "reason": "TD_DISCONNECTED"}
        return trading.submit_manual_order(
            self._adapter, account_id=self._account_id,
            operator=cmd.get("operator") or "manual",
            req_id=cmd.get("req_id") or "", order=o)

    def _handle(self, cmd: dict) -> None:
        verb = cmd.get("verb") or ""
        req_id = cmd.get("req_id") or ""
        self._current_req_id = req_id
        self._current_verb = verb
        if verb == "close-all":
            self._closing.set()      # Gate 覆盖两条链（具名钩子 + process_batch）
            try:
                result = self._close_all(cmd)
            finally:
                self._closing.clear()
            if "error" in result:    # 快速失败（TD 断线）走不到 _close_all_positions 的首轮写
                trading.write_cmd_result(req_id, self._ack(verb, req_id, result))
            # 只有 close-all 有「急停终局」语义（:final + SSE）——cancel-all/未知 verb 只写主回执
            self._final(cmd, result, interrupted=False)
        elif verb == "manual-order":
            # 批173：正常下单也碰 adapter，复用 _closing 短窗防与主循环行情链并发；
            # 只写主回执，不写 :final / 不发 SSE（避免污染急停终局列表）。
            self._closing.set()
            try:
                result = self._manual_order(cmd)
            finally:
                self._closing.clear()
            trading.write_cmd_result(req_id, {"req_id": req_id, "verb": verb,
                                              "phase": "done", **result})
        elif verb == "cancel-all":
            result = self._cancel_all(cmd)
            trading.write_cmd_result(req_id, self._ack(verb, req_id, result))
        else:
            result = {"error": f"unknown verb: {verb}"}
            trading.write_cmd_result(req_id, self._ack(verb, req_id, result))
        self._current_req_id = ""
        self._current_verb = ""

    @staticmethod
    def _ack(verb, req_id, result) -> dict:
        """cancel-all / 未知 verb 的即时主回执（web 5s 窗）。"""
        return {"req_id": req_id, "verb": verb, "phase": "first",
                "result": result, "pending": [], "no_quote": [],
                "retrying": False, "closed_requested": 0, "interrupted": False}

    def _final(self, cmd: dict, result: dict, *, interrupted: bool) -> None:
        """终局：写 :final（TTL 30min）+ uid 定向零内容 SSE 帧（件五）。

        仅 `verb=="close-all"` 有此语义——cancel-all/未知 verb 若也写 :final，`recent`
        端点的 scan 兜底会把「撤单终局」当急停清单上屏（P2-b）。单点在此收口，覆盖
        `_handle` 正常路径与 `_run` 异常兜底路径。
        """
        if (cmd.get("verb") or "") != "close-all":
            return
        req_id = cmd.get("req_id") or ""
        payload = dict(result) if isinstance(result, dict) else {"result": result}
        payload.setdefault("pending", [])
        payload.setdefault("no_quote", [])
        payload.setdefault("retrying", False)
        payload.setdefault("closed_requested", 0)
        payload.setdefault("positions_remain", -1)
        payload.setdefault("interrupted", False)
        payload["interrupted"] = bool(interrupted)
        payload["req_id"] = req_id
        payload["verb"] = cmd.get("verb") or ""
        payload["phase"] = "final"
        # uid 定向（int(sub)，非 0）；缺失/非法/0 → 不发帧、绝不退回 uid=0 全站
        try:
            uid = int(cmd.get("operator_uid"))
        except (TypeError, ValueError):
            uid = 0
        # recent 端点 scan 兜底按 ts 倒序、operator_uid 可过滤（元数据，不进 SSE 帧）；
        # 频道级立法只约束 quant:sse:* 帧，:final 是鉴权端点读的 Valkey 键，不冲突
        payload["ts"] = time.time()
        payload["operator_uid"] = uid if uid else None
        trading.write_cmd_result(req_id, payload, final=True)
        if uid:
            try:
                from src.quant_common.eventbus import bus
                bus.publish_cross_process(uid, "emergency_close_final", {})
            except Exception as e:
                logger.warning("急停终局 SSE 帧发布失败 req=%s: %s", req_id, e)
        else:
            logger.warning("急停终局 operator_uid 缺失/非法（%r）→ 不发 SSE 帧，"
                           "靠 :final 端点 + FE 轮询兜底 req=%s",
                           cmd.get("operator_uid"), req_id)

def _warmup_merge(hist: list, entries: list, upto_ts: str | None = None) -> list:
    """流回放合入 history（模块级可测——批 56b 盲审 A：闭包版不可测致回归钉缩水）。

    entries=[(id, fields)]（xrevrange 原序=新→旧）；upto_ts（epoch 键）截断防未来；
    键值分离：去重/截断走 _epoch_key（跨表示同刻同键），传值走 _norm_ts（UTC ISO）。
    """
    bars = []
    seen = {_epoch_key(h.get("ts")) for h in hist}
    for _id, f in reversed(entries):          # 旧→新
        ts_key = _epoch_key(f.get("ts", ""))
        if upto_ts and ts_key and ts_key > upto_ts:
            continue                          # 只灌当前消息之前的（防未来）
        if ts_key and ts_key not in seen:
            bars.append({"ts": _norm_ts(f.get("ts", "")), "open": float(f["open"]), "high": float(f["high"]),
                         "low": float(f["low"]), "close": float(f["close"]), "volume": float(f["volume"] or 0)})
            seen.add(ts_key)
    hist.extend(bars)
    return hist


def _norm_ts(v) -> str:   # 传值归一：UTC ISO（策略/日志面——形状与批 56b 前一致，仅统一 UTC 表示）
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:
        return str(v)

def _epoch_key(v) -> str:   # 比较键（批 56b：去重/水位/截断——+08:00/Z 混合窗口同刻同键；29 号歧义消解=Unix 秒）
    try:
        return str(int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()))
    except Exception:
        return ""

class BarMsgState:   # worker 侧消息序号/去重状态（gen 分区内 seq 连续，R-BR6/R-DL2）
    def __init__(self):
        self.gen = 0
        self.seq = 0
        self.max_ts: str = ""       # 本标的已处理最大 ts epoch 键（R-DL1 持久去重，评审 S7 真正使用）

    def classify(self, m: dict) -> str:
        gen, seq = int(m["gen"]), int(m["seq"])
        if gen < self.gen:
            return "stale_gen"
        if gen > self.gen:
            self.gen, self.seq = gen, seq
            return "gen_jump"
        if seq <= self.seq:
            return "dup_or_reorder"
        if seq != self.seq + 1:
            self.seq = seq   # 接受跳变（gap 由调用方回放补齐）
            return "gap"
        self.seq = seq
        return "ok"

def _hub_alive(r, account_id) -> bool:   # hub 心跳存在（TTL 内）；存储不可查返回 True（断流自然使 bar 过期）
    try:
        return r.exists(f"{HB_KEY}:{account_id}") == 1   # 批 66b：per-account 键（修原裸键读=A股心跳恒真掩盖加密 hub 死亡）
    except Exception:
        return True

def _ts_gap_frozen(ts_key: str, max_ts: str, market: str) -> bool:
    """批 66c（D26 §3.4①②）：ts 缺口检测——ts_key−max_ts>60s 且 bar 自身时刻在盘中
    连续段内（段首 2 分钟豁免：隔段/隔日/重启首根不触发）。检出=源侧丢根（rewarm 补不了
    的洞——A股 bar_1min 空），比 seq gap 严重：冻结 sticky（重启+rewarm 推进水位=显式
    接受带洞窗，控制目标=强制人工介入）。

    bar 自身 ts 判时段（非 wall clock）——盘外迟到 bar 不触发（in_session(bar_dt)）。
    crypto 24x7 连续段：无段首豁免（60s+ 缺口=真断流即冻结）。
    """
    if not ts_key or not max_ts:
        return False
    try:
        gap = int(ts_key) - int(max_ts)
    except ValueError:
        return False
    if gap <= 60:
        return False
    from datetime import datetime
    from datetime import timezone as _tz
    try:
        bar_dt = datetime.fromtimestamp(int(ts_key), tz=_tz.utc).astimezone()
    except (ValueError, OSError, OverflowError):
        return False
    from src.quant_common.markets import MARKETS
    skel = MARKETS.get(market, {}).get("sessions_skeleton")
    if skel == "24x7":
        return True   # 连续市场：60s+ 缺口=真断流（无段首豁免）
    if not skel:
        return False  # 未知市场无骨架=不判（fail-open 观测面；A 股已覆盖）
    hm = bar_dt.hour * 100 + bar_dt.minute
    for op, cl in skel:
        if int(op.replace(":", "")) <= hm <= int(cl.replace(":", "")):
            return (hm - int(op.replace(":", ""))) >= 2   # 段首 2 分钟内豁免
    return False   # 段外（迟到 bar/盘外）不触发


def _td_connect_due(now: float, win, last_conn_ts: float, dt_now) -> bool:
    """TD 窗开沿建连判定（P2 批 08-28，代码盲审 B-P1）：交易日门（消周末窗读"开"的
    churn 触发）+ 60s 节流（防每步 5s createTraderApi 循环——失败不 release，08-25
    MD 槽回收同型）。纯函数供单测。"""
    if not win or now - last_conn_ts < 60.0:
        return False
    from src.providers.xtp.session import is_trading_day, xtp_session_window_open
    return xtp_session_window_open(dt_now, win[0], win[1], trading_day=is_trading_day())


def run(ctx: dict) -> None:
    """ctx: {tid, sid, symbol, strategy, adapter, event_engine, td_api, history, frozen, warmup_pg, stop_check, reconcile}"""
    # 2026-08-19 归位：直连 quant_common（原经 main 互指且连带加载入口模块级 vnpy import）
    # 批 66c：时段判定 per-market（原 _in_astock_session 对加密 24/7 误伤——盘外 BUY 误拒+
    # STALE_PUB_S 丢弃误前置；market=ctx 注入真源（接口行），缺省按 symbol 后缀派生）
    from src.quant_common.guard import sd_notify as _sd_notify
    from src.quant_common.markets import market_of_symbol
    from src.quant_common.session import in_session, session_edge

    r = _valkey()
    # 批 171s：账号级 Gate（急停整轮期间置位，覆盖两条链）。可从 ctx 注入（测试可持有并复位；
    # 生产 ctx 无该键 → 新建，行为不变）。
    closing = ctx.get("closing") or threading.Event()
    tid, sid, symbol = ctx["tid"], ctx["sid"], ctx["symbol"]
    market = ctx.get("market") or market_of_symbol(symbol)   # 批 66c：ctx 注入真源（接口行），缺省按 symbol 后缀派生
    # 批 118：暖机/运行窗常量真源=src.strategy_runner（WARMUP_BASELINE=100/WARMUP_CAP=5000）。
    # 此处窗长=运行期 history 上限：needs 聚合声明窗与基线取大（needs 随 ctx 注入——
    # runner 装载侧算好；未注入（测试/旧调用方）回退基线窗）。
    from src.strategy_runner import WARMUP_BASELINE
    _needs = ctx.get("needs") or {}
    hist_window = max(WARMUP_BASELINE, int(_needs.get("bar_minute", 0)))

    def _in_mkt_session(now=None) -> bool:
        return in_session(market, now)
    account_id = ctx.get("account_id")     # D26 批 66b：per-account 流键/同源校验真源（全市场；上游 NOT NULL+main 层兜底）
    if account_id is None:
        raise ValueError("ctx.account_id required——裸键形态已退役（批 66b；上游 live_task.account_id NOT NULL）")
    strategy, adapter = ctx["strategy"], ctx["adapter"]
    ee = ctx["event_engine"]          # 评审 C1：键名统一
    exec_port = ctx["exec_port"]      # 172b：事件推送走 ExecutionPort（必填，禁回落直注册）
    td_api = ctx.get("td_api")
    history = ctx["history"]
    frozen = ctx["frozen"]            # 与 _run_hub_mode 的 send_order 网关共享同一 dict（评审 C2）
    stream = bar_stream_key(symbol, account_id)
    gname = f"task-{tid}"
    cname = f"w-{os.getpid()}"
    state = BarMsgState()
    # last_bar_wall=进程累计；sess_bar_wall=时段内基线（S6 修订：沿上清零，昨夜回放 bar 不污染今晨判定）
    stats = {"last_bar_wall": 0.0, "bars": 0, "dropped_stale": 0, "dropped_dup": 0,
             "dropped_cross": 0, "sess_bar_wall": 0.0}
    hb_task_key = f"quant:hb:task:{tid}"

    # ——— EVENT_TRADE → trade_log（评审 S1；4a 单源化 trading；172b 走端口）———
    @make_guard("hub.on_trade", _alert)
    def on_trade(t):
        trading.write_trade_log(t, adapter, sid, symbol)
        # 批 69a：SSE 信号帧（零内容——前端重拉 orders/position）
        try:
            from src.quant_common.eventbus import bus  # 惰性导入（notify.py:89 先例）
            bus.publish_cross_process(0, "trade_update", {})
        except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
            pass

    exec_port.subscribe_trade(on_trade)
    # ——— 消费组（先建组后回放，评审陷阱 6）———
    # P0-3 修复（2026-08-20 双盲审计 A1）：重启即销毁旧组重建（新组从 $ 起）——原组残留时 ">" 从旧水位
    # 续消费，重启窗内 bar 重驱动 on_bar=重复下单；丢的 bar 由暖机/gap 从 DB 补，双保险=下方 max_ts 过滤。
    try:
        r.xgroup_destroy(stream, gname)
    except Exception:  # noqa: S110
        pass   # 组不存在（首启）属正常  # 失败不阻断（fail-open 降级）  # noqa: S110
    try:
        r.xgroup_create(stream, gname, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.warning("XGROUP CREATE 失败: %s", e)

    # P0-3 配套：max_ts 持久化恢复（R-DL1 跨重启去重——曾进程内存失忆）
    # 批 66b：水位键全市场统一含 account（旧 A股裸水位键失联=重新暖机，部署窗外切换无害）
    _mts_key = f"hub:worker:max_ts:{account_id}:{symbol}"
    try:
        _saved = r.get(_mts_key)
        if _saved:
            # 批 56b 兼容：存量水位是 ISO（+08:00 表示）→转 epoch 键；转不动=脏值从零起（防恒假去重）
            state.max_ts = _saved if _saved.isdigit() else _epoch_key(_saved)
            logger.info("跨重启水位恢复 max_ts=%s", state.max_ts)
    except Exception as e:
        logger.warning("水位恢复读取失败（从空水位起）: %s", e)
    # ——— 暖机：只填 history 绝不调 on_bar（评审 F3）———
    def _warmup_from_stream(hist: list, upto_ts: str | None = None) -> list:
        """流回放填 history。upto_ts（epoch 键）截断（rewarm 时防未来泄漏，评审 S4）。
        合入逻辑在模块级 _warmup_merge（可测——批 56b 盲审 A 修）。
        回放窗=hist_window（needs 聚合声明窗与基线取大，批 118——原硬编码 240 根
        恒小于基线窗 100 时首启即缺根；常量真源见 src.strategy_runner）。"""
        try:
            entries = r.xrevrange(stream, count=hist_window)
            before = len(hist)
            _warmup_merge(hist, entries, upto_ts)
            logger.info("hub 暖机：流回放补 %d 根（history 总 %d，回放窗 %d）",
                        len(hist) - before, len(hist), hist_window)
        except Exception as e:
            logger.warning("hub 暖机回放失败: %s", e)
        return hist

    def _rewarm(upto_ts: str | None = None) -> None:
        fresh = ctx["warmup_pg"]()
        fresh = _warmup_from_stream(fresh, upto_ts)
        fresh = fresh[-hist_window:]
        # 批 118 判定三分支·补充分支：rewarm 后窗口 < 声明窗 ⇒ 视同分支② degraded
        # （瞬态——流回放窗有限是常态，不升级为拒启）。仅告警+记档，不冻结不退出；
        # 首启判定已在 runner 装载侧（judge_supply），此处收运行期收缩面。
        _declared = int(_needs.get("bar_minute", 0))
        _daily_declared = int(_needs.get("bar_daily", 0))
        _strat = ctx.get("strategy")
        _shrunk = bool(_declared and len(fresh) < _declared)
        # 批 119（复审 P1-3 补）：日线轴同判——原只看 bar_minute ⇒ 初始 _rewarm() 启动即跑会把
        # 启动时判定的 degraded 覆盖成 False（日线窗仍空）⇒ 因子异常重新以 0.0 进聚合（「降级=下单」雷换日线面复发）
        _daily_short = bool(_daily_declared and len(getattr(_strat, "_daily_history", None) or []) < _daily_declared)
        if _shrunk:
            logger.warning("rewarm 后窗口 %d < 声明窗 %d（%s）——degraded：因子窗口不足，"
                           "值可能不准；数据面恢复后自动补齐", len(fresh), _declared, symbol)
            try:
                from src.strategy_runner.main import judge_supply
                d = judge_supply(symbol, _needs, len(fresh),
                                 daily_actual=len(getattr(_strat, "_daily_history", None) or []))
                _alert(f"任务 {tid} rewarm 窗口不足（{symbol}）",
                       f"{d['reason']}（实得 {d['actual']}/{d['declared']}）", code="warmup.degraded")
                _st = getattr(_strat, "warmup_result", None)
                if isinstance(_st, dict):
                    _st.update(d)
            except Exception:   # noqa: S110  # 告警链 never-raise（fail-open 降级）
                pass
        # 批 119（独立补审 P1-3）：ctx.supply_degraded 承诺「随 rewarm 更新」——原只启动赋值一次，
        # 运行期收缩后降级通道仍 False ⇒ compute_factors 照旧吞异常成 0.0 进聚合（「降级=下单」雷）。
        # 双向同步：收缩=True；窗口恢复=False（数据面自愈后可翻回正常通道）。
        if _strat is not None:
            _strat._supply_degraded = bool(_shrunk or _daily_short)
        history[:] = fresh
        # 批 66c 解冻闭环（D26 §3.4②）：rewarm 后显式推进 max_ts 至回放尾——否则回放不推水位，
        # 冻结重启后首根 live bar 仍满足缺口条件再冻结（盘中永远解不了）。跳洞=显式接受。
        if history:
            cand = _epoch_key(history[-1].get("ts", ""))
            if cand and (not state.max_ts or int(cand) > int(state.max_ts)):
                state.max_ts = cand
                try:
                    r.set(_mts_key, cand)   # 持久水位同步（重启一致性）
                except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
                    pass

    _rewarm()   # 初始暖机（消费组建在 $，流内现有 bar 全部是"过去"，无未来泄漏）
    # ——— 批 76 F1：事实表记账/闭环（fail-soft——冻结优先于记账，写库失败只告警不上抛） ———
    def _record_freeze(freeze_type: str, *, gap_target_ts: str | None = None,
                       detail: dict | None = None) -> None:
        try:
            from src.data_platform.freeze_event import record_freeze
            record_freeze(tid, symbol, freeze_type, account_id=account_id,
                          watermark=state.max_ts or None, gap_target_ts=gap_target_ts,
                          detail=detail)
        except Exception as e:
            logger.warning("冻结事件记账失败（不阻断冻结）: %s", e)

    def _close_freeze(method: str, operator: str | None = None) -> None:
        try:
            from src.data_platform.freeze_event import close_freeze
            n = close_freeze(tid, method=method, operator=operator, symbol=symbol)
            if n:
                logger.info("冻结事件闭环 %d 行（method=%s operator=%s）", n, method, operator)
        except Exception as e:
            logger.warning("冻结事件闭环失败（不阻断解冻）: %s", e)

    # 启动补记：上一进程若冻结着退出（正常停/被杀皆然），其进行中事件随本次启动闭环为 restart。
    # 本进程尚未冻结 ⇒ 该 tid 的任何进行中事件必属上一进程，归属无歧义。
    _close_freeze("restart")

    # ——— send_order 时刻事实检查（S6）：交易时段+bar 新鲜+hub 心跳；纯逻辑在 trading.buy_ok_check，检查器由 ctx 注入 C2 网关 ———
    ctx["buy_ok"] = lambda: trading.buy_ok_check(frozen, stats, _hub_alive(r, account_id), time.time(),
                                                in_session=_in_mkt_session())
    # ——— 消息处理（guard 保护，R-BR12；告警走 _alert——notify 自带 1min 同标题去重）———
    @make_guard("hub.on_msg", _alert)
    def handle_msg(fields: dict) -> None:
        # D26 批 66b 同源校验（全市场）：流消息必带本 account_id（payload 契约），跨源 fail-fast 不落地
        if _account_mismatch(fields, account_id):
            stats["dropped_cross"] += 1
            logger.error("跨源消息拒绝（account_id=%s != %s）: %s",
                         fields.get("account_id"), account_id, fields.get("ts"))
            return
        ts_key = _epoch_key(fields.get("ts", ""))
        # R-DL1 持久去重（评审 S7）：ts 回退/重复（含 flush 迟到 tick 重复桶）一律丢弃（epoch 键——跨表示同刻同键）
        if ts_key and ts_key <= state.max_ts:
            stats["dropped_dup"] += 1
            return
        # 批 66c：ts 缺口检测（时段感知+段首豁免——裸比较会在午休/隔夜/节假日每日误触发=
        # 告警疲劳+rewarm 风暴，D26 §3.4①）。检出=sticky 冻结（比 gen_jump 档严重：源侧丢根
        # rewarm 补不了；重启+rewarm 推进水位过缺口=操作者显式接受带洞窗）
        if _ts_gap_frozen(ts_key, state.max_ts, market):
            frozen["sticky"] = True
            frozen["sticky_cause"] = "ts_gap"     # 批 76：冻结成因（人工解档——自动解只对 seq_gap）
            logger.error("ts 缺口 %s→%s（源侧丢根），冻结", state.max_ts, ts_key)
            _alert(f"ts 缺口冻结任务 {tid}（{symbol}）",
                   f"水位 {state.max_ts} 后缺口至 {ts_key}（源侧丢根，rewarm 不可补）；"
                   "请在 Web/IM 人工解冻（显示接受带洞窗）——SELL 放行。", code="frozen.stream")
            try:
                _gap_s = int(ts_key) - int(state.max_ts)
            except (TypeError, ValueError):
                _gap_s = None
            _record_freeze("ts_gap", gap_target_ts=ts_key,
                           detail={"watermark": state.max_ts, "gap_s": _gap_s})
            _rewarm(upto_ts=ts_key)   # 推进水位过缺口（历史面尽力补）——sticky 已拒 BUY
            return
        kind = state.classify(fields)
        if kind == "stale_gen":
            logger.warning("拒绝旧代次消息 gen=%s < %s（fencing）", fields["gen"], state.gen)
            return
        if kind == "gen_jump":
            # 数据恢复：重暖机补缺口（从 bar 流触发是正确的——只有消费侧知道缺口多大）
            # 告警：hub 重启是基础设施生命周期事件，由 hub 自身(monitor 断流检测+启动告警)报告，
            #   不从消费侧推断（arch-13 审查设计纠偏+用户架构直觉确认）——worker 重启后首根 gen
            #   从 0 跳到当前值是必然，从这发告警=误报（盲审 A-P2① 的根修）
            logger.info("hub 代次切换 -> gen=%s，重暖机补缺口", state.gen)
            _rewarm(upto_ts=ts_key)
        elif kind == "dup_or_reorder":
            stats["dropped_dup"] += 1
            return
        elif kind == "gap":
            logger.warning("seq 跳变（gap），重暖机并冻结，待流衔接后自动解冻")
            frozen["sticky"] = True
            frozen["sticky_cause"] = "seq_gap"    # 批 76：自动解档（衔接判定通过即清）
            _rewarm(upto_ts=ts_key)
            _record_freeze("seq_gap", gap_target_ts=ts_key,
                           detail={"watermark": state.max_ts})
            _alert(f"流序号跳变，任务 {tid} 冻结（流衔接后自动解冻）",
                   "rewarm 已补历史；后续 bar 连续即自动解冻，无需人工。", code="frozen.stream")
        # ——— 批 76 F2：衔接判定（自动解冻）———
        # 到达此处 = 本根既未触发 ts 缺口（上方已过）、又续上了序号（ok/gen_jump）⇒ 流真的回来了。
        # 仅对 seq_gap 档自动解（rewarm 已补历史=数据面已无残留）；ts_gap/untrusted 不信自证，人工解。
        if (kind in ("ok", "gen_jump") and frozen.get("sticky")
                and frozen.get("sticky_cause") == "seq_gap"):
            frozen["sticky"] = False
            frozen["sticky_cause"] = None
            logger.warning("流已衔接（kind=%s），自动解冻任务 %s", kind, tid)
            _alert(f"任务 {tid} 流已衔接，自动解冻",
                   "seq 跳变冻结随流恢复自动解除（rewarm 已补历史；BUY 恢复）。", code="frozen.auto")
            _close_freeze("auto_reconnect")
        # pub_ts 超龄丢弃（R-DL3）
        pub_ts = float(fields.get("pub_ts", 0) or 0)
        if pub_ts and _in_mkt_session() and (time.time() - pub_ts) > STALE_PUB_S:
            stats["dropped_stale"] += 1
            return
        if str(fields.get("untrusted", "0")).lower() in ("1", "true"):
            frozen["sticky"] = True
            frozen["sticky_cause"] = "untrusted"   # 批 76：人工解档（污染事实不自动翻案）
            logger.error("untrusted bar（断线失真），冻结: %s", fields.get("ts"))
            _alert(f"不可信 bar，冻结任务 {tid}（{symbol}）",
                   "断线跨分钟失真，请在 Web/IM 人工解冻（显示接受当前数据）。", code="frozen.stream")
            _record_freeze("untrusted", gap_target_ts=ts_key or None)
            return
        bar = {"ts": _norm_ts(fields.get("ts", "")), "open": float(fields["open"]), "high": float(fields["high"]),
               "low": float(fields["low"]), "close": float(fields["close"]),
               "volume": float(fields["volume"] or 0)}
        # bar 已接受：先落锚+去重水位再驱动策略（盲审 C-F1——锚后更新则首根 bar 的 BUY 读到昨日旧锚被确定性误拒）
        stats["last_bar_wall"] = time.time()
        if _in_mkt_session():
            stats["sess_bar_wall"] = stats["last_bar_wall"]
        if ts_key:               # 批 56b 盲审 B：空键不回写内存与 Valkey（脏 ts 不清零水位——R-DL1 失忆防线）
            state.max_ts = ts_key
            try:
                r.set(_mts_key, ts_key)   # P0-3：水位持久化（重启恢复，防 SELL 重放；失败不阻断）——epoch 键
            except Exception:  # noqa: S110
                pass  # 失败不阻断（fail-open 降级）  # noqa: S110
        # 批 62a：血缘注入（A03 §15.4）——pub_ts=流到端时刻（fetched_at 真源）/gen=流世代
        # （dataset_version 源）；与 bar 同点更新（strategy 实例属性，_log_signal_order 消费）
        try:
            strategy._pub_ts = datetime.fromtimestamp(pub_ts, tz=timezone.utc) if pub_ts else None
            strategy._hub_gen = fields.get("gen")
        except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
            pass
        try:
            sig = strategy.on_bar(bar, list(history))
        finally:
            # 批82（P0-2 盲审 P0-A/B）：on_bar 异常也补 history 护后续因子窗口——bar 已接收，
            # 照常推水位+xack（不重放：重放经 classify seq 去重吞掉、且 max_ts 不推会触发缺口误冻结）。
            # 该 bar 信号损失由 guard 的 logger.exception + safe_notify critical 告警暴露。
            stats["bars"] += 1
            history.append(bar)
            if len(history) > hist_window:   # 批 118：运行窗=needs 聚合声明窗（原硬编码 100）
                history.pop(0)
        sa = getattr(sig, "action", None)
        logger.info("BAR %s close=%.2f vol=%.0f signal=%s", bar["ts"][:19], bar["close"], bar["volume"],
                    sa.name if sa else "NONE")

    def process_batch(batch) -> int:
        # 批 171s 件四（审官 B P0-B）：急停整轮期间**只 xack、不驱动策略**——
        # 不调 handle_msg/on_bar/place_order ⇒ strategy.py:496 send_order 零调用；
        # bar 不丢（xack 后由暖机/gap 从 DB 补，同 :288 既有双保险）。
        # 不改 xsleeper 单线程模型、不引后台线程进行情链。
        # 逐条判 Gate（批次可能跨置位时刻：入口判会让后半批继续驱动 on_bar）。
        ids = []
        processed = 0
        for _stream, entries in batch:
            for eid, f in entries:
                if not closing.is_set():
                    handle_msg(f)
                    processed += 1
                ids.append(eid)   # 急停期同样 xack（不背 pending）
        if ids:
            try:
                r.xack(stream, gname, *ids)
            except Exception as e:
                logger.warning("XACK 失败: %s", e)
        return processed

    # ——— 批 4b：EngineLoop 编排（旧 5s 定时段逐项退化为钩子；11 项清单/period 见设计）———
    sess_was = _in_mkt_session()   # 时段沿检测基态
    td_status_was = True
    _td_conn_ts = [0.0]   # TD 窗开建连 60s 节流锚（P2 批 08-28，B-P1；容器型便 nonlocal 闭包）
    halt_state = {"was": False}
    _baseline_cache = {"baseline": None}   # 账户基线缓存（4a 起调用方持有）
    hb = HeartbeatWriter(r, hb_task_key, ttl=90)

    # 批 171s 件四：账号内急停专用 daemon 线程 + 限时 drain 参数（drain_timeout_s）
    try:
        from src.data_platform.hub_quote import get_retry_config
        emergency_drain_s = float(get_retry_config().get("drain_timeout_s") or 5)
    except Exception:
        emergency_drain_s = 5.0
    emergency = EmergencyWorker(adapter=adapter, td_api=td_api, account_id=account_id,
                                tid=tid, closing=closing)

    def _drain_before_exit(where: str) -> None:
        """exit_process(0) 前置位 + 限时 drain；超时写 :final(interrupted) + _alert
        + order_log 标 interrupted（件四：不留半截平仓无痕、never-silent）。"""
        req_id = emergency.current_req_id
        verb = emergency.current_verb
        closing.set()
        if emergency.drain(emergency_drain_s):
            return
        if verb != "close-all":
            # 非 close-all（撤单/未知）无急停终局语义——不写 :final（防 recent scan 误上屏）
            logger.error("急停线程 drain 超时（%s，%ss）verb=%s req=%s——非 close-all，不写终局",
                         where, emergency_drain_s, verb, req_id)
            return
        logger.error("急停线程 drain 超时（%s，%ss）req=%s", where, emergency_drain_s, req_id)
        _alert(f"任务 {tid} 急停线程 drain 超时（{where}）",
               f"req={req_id}；在飞平仓已标 interrupted，请人工核对券商委托。",
               code="emergency.interrupted")
        trading.write_cmd_result(
            req_id,
            {"req_id": req_id, "verb": "close-all", "phase": "final",
             "interrupted": True, "retrying": True,
             "pending": [], "no_quote": [], "closed_requested": 0,
             "positions_remain": -1},
            final=True)
        trading.mark_emergency_interrupted(req_id)

    def _stop_hook():
        """停止路径（设计裁定不用 failure=exit——Restart=on-failure 拉起=F-36 churn 倒退）：
        清理=旧 finally 语义（xgroup_del）+ os._exit(0) 正常停止码（SA4 分类，不触发重启）。"""
        if not ctx["stop_check"]():
            return
        logger.info("任务 %s 收到停止，退出", tid)
        from src.strategy_runner.main import _log_timeline
        _log_timeline(tid, "info", "任务停止：退出码 0（stop_live_task 置 stopped，清理 xgroup 后退出）")
        try:
            r.xgroup_del(stream, gname)
        except Exception:  # noqa: S110
            pass  # 失败不阻断（fail-open 降级）  # noqa: S110
        _drain_before_exit("stop")
        exit_process(0)

    def _sess_edge():
        nonlocal sess_was
        sess_now = _in_mkt_session()
        if session_edge(sess_now, sess_was):
            stats["sess_bar_wall"] = 0.0   # S6 修订：沿上清基线
        sess_was = sess_now

    def _blind_watch():
        """盲视观测（S6）：frozen["now"] 只喂心跳/告警，下单判定由 send_order 时刻的 buy_ok 做。"""
        sess_now = _in_mkt_session()
        hub_alive = _hub_alive(r, account_id)
        bar_stale = (sess_now and stats["sess_bar_wall"]
                     and time.time() - stats["sess_bar_wall"] > trading.FROZEN_STALE_BAR_S)
        new_dyn = (not hub_alive) or bool(bar_stale)
        if new_dyn and not frozen.get("now"):
            logger.error("盲视状态（hub_alive=%s bar_stale=%s）——BUY 将在下单时刻被拒",
                         hub_alive, bool(bar_stale))
            _alert(f"任务 {tid} 盲视（hub{'心跳丢失' if not hub_alive else ' bar 停更'}）",
                   "BUY 在下单时刻被拒/SELL 放行；数据恢复自动解除。", code="buy.blind")
        frozen["now"] = new_dyn or bool(frozen.get("sticky"))

    def _heartbeat():
        """心跳（D3 定案）：只写 worker 自有字段+ts（md 字段区分模式；无 tick 源不冒充）。

        批 69b：td 导出（TD 会话真连态——L2 真连探测消费；探测导出缺省 False〔td_api
        None=stub〕，与 _td_reconnect 沿判定缺省 True 目的不同〔沿判定乐观重试〕）。
        批 171o：working_orders 导出（在场委托 working 态计数——紧急处置页 exposure
        数据源；超集原则只增字段，collector hgetall 兼容）。
        """
        _wo = -1   # -1=未知（TD 断线/查询失败——渲染「未知」非 0，A-P0-2 防线）
        try:
            # 批 171s：心跳不整跳（必须继续，防误判僵尸），仅急停期跳过
            # adapter.query_orders()（同账号 adapter 读归专用线程），working_orders 记 -1
            if getattr(td_api, "connect_status", False) and not closing.is_set():
                from vnpy.trader.constant import Status as _St
                _wo = sum(1 for od in (adapter.query_orders() or [])
                          if getattr(od, "status", None) in
                          (_St.SUBMITTING, _St.NOTTRADED, _St.PARTTRADED))
        except Exception:
            _wo = -1
        hb.beat(pid=os.getpid(), md="hub", gen=state.gen,
                last_bar_ts=stats["last_bar_wall"] or 0,
                lag=(time.time() - stats["last_bar_wall"]) if stats["last_bar_wall"] else -1,
                bars=stats["bars"], frozen=int(frozen.get("now", False)),
                td=int(bool(getattr(td_api, "connect_status", False))),
                working_orders=_wo)

    def _td_reconnect():
        """TD 重连沿 → 重跑对账（R-BR11；ctx["reconcile"]=runner 超集含成交补录，4b 收敛冗余循环）。
        窗开沿建连（P2 批 08-28，A-P1-1）：窗开且未连 → ctx["td_connect"]()——盘外窗关
        启动的进程由此腿补首连，连上后下一沿触发对账（B-P1-1 启动对账条件化的配对）。"""
        nonlocal td_status_was
        if closing.is_set():
            return   # 批 171s：急停期跳过 td-reconnect / reconcile（adapter 读归专用线程）
        td_status = bool(getattr(td_api, "connect_status", True))
        if td_status and not td_status_was:
            logger.info("TD 重连沿，重跑对账（含成交补录）")
            try:
                ctx["reconcile"]()
            except Exception as e:
                logger.warning("重连对账失败: %s", e)
        if not td_status:
            from datetime import datetime as _dt
            if _td_connect_due(time.time(), ctx.get("td_window"), _td_conn_ts[0], _dt.now()):
                _td_conn_ts[0] = time.time()
                logger.info("TD 连接窗开沿，建连（60s 节流+交易日门）")
                try:
                    ctx["td_connect"]()
                except Exception as e:
                    logger.warning("TD 窗开建连失败（60s 后重试）: %s", e)
        td_status_was = td_status

    def _zombie_claim():
        """僵尸 pending 认领并处理（评审 S3：认领即消费，幂等靠 ts 去重）。"""
        try:
            _next, claims = r.xautoclaim(stream, gname, cname, min_idle_time=60000, count=20)
            if claims:
                process_batch([(stream, claims)])
        except Exception:  # noqa: S110
            pass  # 失败不阻断（fail-open 降级）  # noqa: S110

    def _unfreeze_poll():
        """批 76 F2：人工解冻请求消费（Web 端点/IM 指令写 `hub:unfreeze:{tid}` → 本钩子 GETDEL）。

        这是「任务内解冻」的落地：外界够不着进程内 frozen 变量，故以 Valkey 请求键为通道，
        5s 钩子原子取删消费（重复/并发请求只有一个生效）。未冻结时的请求=空操作（记日志）。
        """
        try:
            from src.data_platform.freeze_event import UNFREEZE_REQ_KEY
            _k = UNFREEZE_REQ_KEY.format(tid=tid)
            try:
                raw = r.getdel(_k)
            except AttributeError:            # 老驱动回退（非原子）
                raw = r.get(_k)
                if raw:
                    r.delete(_k)
        except Exception as e:                # 存储不可查/网络抖动：下轮重试（请求键有 TTL 兜底）
            logger.debug("解冻请求轮询失败: %s", e)
            return
        if not raw:
            return
        try:
            payload = json.loads(raw) if isinstance(raw, str) else {}
        except (ValueError, TypeError):
            payload = {}
        operator = payload.get("operator") or "?"
        channel = payload.get("channel") or "manual_web"
        if frozen.get("sticky"):
            frozen["sticky"] = False
            frozen["sticky_cause"] = None
            logger.warning("人工解冻（channel=%s operator=%s）——操作者已显式接受当前数据状态",
                           channel, operator)
            _alert(f"任务 {tid} 人工解冻", f"操作者 {operator}（{channel}）已接受当前数据状态，BUY 恢复。",
                   code="frozen.manual")
            _close_freeze(channel, operator=operator)
        else:
            logger.info("收到解冻请求但当前未冻结（忽略）: %s", payload)

    def _on_fatal(reason: str) -> None:
        # 批 171s：loop._fatal 在 on_fatal 之后立即 exit_process（os._exit 强杀在飞线程）
        # ⇒ 必须先限时 drain 留痕，再发原告警。
        _drain_before_exit("fatal")
        _alert(f"任务 {tid} {reason}，自动重启",
               "worker 退出由 systemd 接管；请查 journalctl 定位首个异常。",
               code="runtime.fatal")

    # P2b：启动规则完整性预检（动态规则注册 + 在跑标的规格齐全）
    try:
        from src.data_platform.rules import assert_dynamic_rules_complete, rules_for
        assert_dynamic_rules_complete()
        _rs = rules_for(account_id, symbol, provider=getattr(adapter, "close_policy_provider", None))
        if _rs.state == "missing":
            _alert(f"任务 {tid} 规则不完整（{symbol}）",
                   "规格缺失=开仓将 fail-closed；请检查 security_master/band_rules。",
                   code="rules.missing")
        elif _rs.state == "stale":
            _alert(f"任务 {tid} 规则快照过期（{symbol}）",
                   "规格表读取失败，正在沿用上次成功值；请检查数据库连通性。",
                   code="rules.stale")
        if getattr(_rs.ins, "venue_flags", {}).get("tick_unverified"):
            _alert(f"任务 {tid} 品种 tick 未核验（{symbol}）",
                   "security_master.tick_size 为历史填充值，实盘前必须按交易所步长校正。",
                   code="rules.tick_unverified")
    except Exception as e:   # noqa: BLE001 —— 预检失败不阻断启动，但必须可审计
        logger.error("规则完整性预检失败: %s", e)
        _alert(f"任务 {tid} 规则预检失败", str(e)[:300], code="rules.preflight")

    loop = EngineLoop(
        name=f"live-task-{tid}", step=5.0,
        sleeper=XReadSleeper(r, stream, gname, cname, process_batch),  # 流消费=sleeper 注入
        watchdog=lambda: _sd_notify("WATCHDOG=1"), event_engines=(ee,),  # 喂狗+事件线程存活（R-BR12）
        on_fatal=_on_fatal)
    loop.every("stop-check", 5.0, _stop_hook)        # 停止（P4-3；清理+exit 0 在钩子内）

    _cfg_seen = {"v": None}

    def _cfg_version_hook():
        """P2b：配置版本轮询（≤2s）——变化即清规格缓存，全进程读到新规格。"""
        try:
            from src.data_platform.routing import CFG_VERSION_KEY
            raw = r.get(CFG_VERSION_KEY)
            ver = int(raw) if raw and str(raw).isdigit() else 0
        except Exception as e:   # noqa: BLE001 —— 读失败下轮再试
            logger.debug("cfg:version 轮询失败: %s", e)
            return
        if _cfg_seen["v"] is None:
            _cfg_seen["v"] = ver
            return
        if ver != _cfg_seen["v"]:
            _cfg_seen["v"] = ver
            try:
                from src.data_platform import specs
                specs.invalidate()
            except Exception as e:   # noqa: BLE001
                logger.warning("规格缓存失效失败: %s", e)
            logger.info("cfg:version=%s 变化，规格缓存已失效", ver)

    loop.every("cfg-version", 2.0, _cfg_version_hook)

    _slot = ctx.get("session_slot")

    def _slot_renew_hook():
        """P3：TD 会话租约续租（15s/45s）；被抢=立即断开退出（75 → systemd 退避重拉）。"""
        if _slot is None:
            return
        from src.strategy_runner.session_slot import SessionLost, SessionSlotAllocator, SessionUnavailable

        try:
            SessionSlotAllocator.renew(_slot)
        except SessionLost:
            _alert(f"任务 {tid} 会话号被抢占（terminal_id={_slot.terminal_id}）",
                   "另一实例已接管该终端号，本进程立即退出避免双会话。", code="slot.lost")
            exit_process(75)
        except SessionUnavailable as e:
            logger.warning("会话租约续租失败（下轮重试）: %s", e)

    loop.every("slot-renew", 15.0, _slot_renew_hook)

    def _daily_roll():
        """批 119：daily_history 日界滚动（v2 复审 D3 钉死）——**每步求值，不依赖 session
        沿翻转**（crypto in_session 骨架 24x7 恒 True 永无翻转，而加密恰是预期消费者）。

        独立补审 P1-2 两修：①**同日只滚一次**——原守卫 `tail < bar_day` 在交易日永远为真
        （tail=上一交易日、bar_day=当日），period=0.0 钩子每 5s 拉一次 DataBus（任务数×12 次/分）；
        改用 `_last_roll_day` 状态位去重。②**空窗不自锁**——原 `if not dh: return` 使暖机失败后
        终身无日线；改为 dh 空也尝试补（有 needs 声明时）。
        """
        try:
            need_n = int(getattr(strategy, "_daily_n", 0) or 0)
            if need_n <= 0:
                return                      # 无声明=零动作
            lb = getattr(strategy, "_last_bar", None) or {}
            ts = lb.get("ts")
            if not ts:
                return
            bar_day = ts[:10] if isinstance(ts, str) else ts.strftime("%Y-%m-%d")
            if getattr(strategy, "_daily_rolled_day", None) == bar_day:
                return                      # 今日已滚（去重）
            dh = getattr(strategy, "_daily_history", None) or []
            from src.strategy_framework.factor import _daily_ts_key
            tail = _daily_ts_key(dh[-1]) if dh else None
            if tail is not None and tail >= bar_day:
                return                      # 窗已含当日（异常态，交由判定面）
            from src.strategy_runner.main import _warmup_daily
            fresh = _warmup_daily(strategy.symbol, need_n)
            if fresh:
                strategy._daily_history = fresh
                strategy._daily_rolled_day = bar_day   # 成功才记，失败下步重试
        except Exception as e:   # 滚动失败保持旧窗（降级不中断——supply_degraded 语义另走判定面）
            logger.warning("daily_history 日界滚动失败（保持旧窗，下步重试）: %s", e)

    loop.every("daily-roll", 0.0, _daily_roll)        # 批 119：日线窗日界滚动：每步
    loop.every("sess-edge", 0.0, _sess_edge)         # 时段沿清 sess_bar_wall 基线：每步
    loop.every("blind-watch", 0.0, _blind_watch)     # 盲视判定+告警（喂 frozen 字段）：每步
    loop.every("heartbeat", 5.0, _heartbeat)         # 心跳（D3 七字段+ts；急停期不整跳）

    def _snapshot_guarded():
        if closing.is_set():   # 批 171s：急停期跳过快照（adapter.query_account/position 归专用线程）
            return
        trading.snapshot_cycle(adapter, ctx.get("account_id"), tid, _baseline_cache)

    def _halt_edge_guarded():
        if closing.is_set():   # 批 171s：急停期跳过熔断沿撤单（撤单归专用线程）
            return
        trading.halt_edge_cancel(adapter, halt_state, sid)

    loop.every("snapshot", 60.0, _snapshot_guarded)   # 快照+持仓批（旧 12 拍=60s）
    loop.every("halt-edge", 0.0, _halt_edge_guarded)  # 熔断沿撤在场单
    loop.every("factor-recalc", 5.0, lambda: trading.recalc_hook(r, _rewarm, history))  # 因子重算+热重载
    loop.every("td-reconnect", 0.0, _td_reconnect)   # TD 重连沿对账：每步
    loop.every("zombie-claim", 5.0, _zombie_claim)   # xautoclaim 僵尸认领（评审 S3）
    loop.every("unfreeze-poll", 5.0, _unfreeze_poll)  # 批 76 F2：人工解冻请求消费（Valkey 键）

    def _risk_cmd_poll():
        """批 171s：急停命令消费——主循环拍只 GETDEL→投递→立即返回（1s 注册保持）。"""
        try:
            consume_risk_cmd(r, adapter, tid, account_id=account_id, td_api=td_api,
                             cmd_queue=emergency.cmd_queue)
        except Exception as e:   # noqa: BLE001 —— 命令通道故障不伤主循环
            logger.debug("急停命令轮询失败: %s", e)

    loop.every("risk-cmd-poll", 1.0, _risk_cmd_poll)   # 批 171n：秒级响应（period=0.0 每步太密）
    emergency.start()   # 批 171s：随 loop 启动（daemon；每 runner 恰一个）
    try:
        loop.run()   # 永续（XReadSleeper never-raise 保证 sleep 位不抛；停止/NOGROUP 带码直达）
    except KeyboardInterrupt:
        pass
    finally:   # 原生库拆除规避（同 hub/direct）；正常停止/NOGROUP 已在钩子/sleeper 内带码直达
        # wd-20 §1.5 裁定②：崩溃退出留痕（finally 捕获在飞异常——os._exit 前最后窗口）
        import sys as _sys
        _exc = _sys.exc_info()[0]
        if _exc is not None:
            try:
                from src.strategy_runner.main import _log_timeline
                _log_timeline(tid, "error", f"异常退出：{_exc.__name__}: {str(_sys.exc_info()[1])[:150]}")
            except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
                pass
        try:
            r.xgroup_del(stream, gname)
        except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
            pass
        _drain_before_exit("exit")   # 批 171s：异常/KeyboardInterrupt 路径同样限时 drain
        exit_process(0)
