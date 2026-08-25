"""XTP 行情 SDK 生命周期守卫（L2 会话层，2026-08-25 SEGV 事故的终结防御）。

事故回顾：setHeartBeatInterval 在 createQuoteApi 之前调用（凭官方文档「Login 前设置」
从引擎外部调）→ C 对象未建即触 → SEGV×5 → hub 停 20 分钟。pybind 封装不提供任何
调用时刻保护——「可以调」的方法都有「可以调的时刻」，非法时刻调用不是异常是段错误。

本守卫把官方时序全部收进状态机：
- IDLE→CREATED：createQuoteApi（此后 C 方法才可安全触达）
- CREATED→LOGGED_IN：login（quote login 同步返回，0=成功）
- LOGGED_IN→CREATED：logout 清场 / onDisconnected

官方语义出处（docs/reference/xtp-sdks/XTPXQuoteAPI…/header/xtpx_quote_api.h）：
- Login 返回 -2：已存在连接，不允许重复登录，如果需要重连，请先 logout（:418）
- SetHeartBeatInterval：必须在 Login 之前调用（:316）——完整语义是
  createQuoteApi 之后、Login 之前，该窗口只存在于本类 connect() 内
- Logout：同步阻塞，禁止在回调线程调用（:431）

状态机有意不含 RELOGGING：quote login 同步返回，logout→login 在 relogin() 一次
调用内完成，中间态对外不可观测（环保：不留无人能看见的状态）。

线程模型（双盲审 P1，2026-08-25）：引擎面 connect/relogin 与 SDK 回调面
onDisconnected/login_server 全程 RLock 互斥；旧会话断开余音（登录成功后 <5s
到达且现态 LOGGED_IN）跳过父类自动重登，防新鲜会话被 churn。
"""
from __future__ import annotations

import enum
import logging
import threading
import time

from vnpy.trader.utility import get_folder_path
from vnpy_xtp.gateway.xtp_gateway import PROTOCOL_VT2XTP, XtpMdApi

logger = logging.getLogger("md_api_guard")


class SdkState(enum.Enum):
    IDLE = "idle"          # C 对象未建——此态任何 C 方法调用都可能 SEGV
    CREATED = "created"    # createQuoteApi 已建，未登录
    LOGGED_IN = "logged_in"
    DEAD = "dead"          # 已关闭（close() 落位；此后 relogin/login_server 必拒）


class SdkLifecycleError(RuntimeError):
    """SDK 非法时刻调用——Python 异常拦截，永不到 C 层。"""


class GuardedXtpMdApi(XtpMdApi):
    """XTP 行情 API 守卫子类。

    引擎与 MdSession 拿到的对象就是本守卫，结构上无法绕过时序：
    - ``connect()``：唯一建 C 对象与首登入口（createQuoteApi→心跳→login）
    - ``relogin()``：引擎面（严格态校验，非法态抛 SdkLifecycleError）
    - ``login_server()``：SDK 回调线程面（父类 onDisconnected 调用它）——**永不抛**
    - ``subscribe()``：软防护，非 LOGGED_IN 态 no-op（周期幂等重放需要，勿抛）

    线程模型（双盲审 P1，2026-08-25）：引擎线程（connect/relogin）与 SDK 回调线程
    （onDisconnected→login_server）全程 RLock 互斥——重登序列（logout→login）不再
    与回调驱动的自动重登交叉篡改状态；RLock 因 onDisconnected 持锁期间父类虚分派
    回本类 login_server 再入同锁。
    """

    HEARTBEAT_INTERVAL = 15   # 秒；SDK 对死链的察觉窗口
    DISCONNECT_ECHO_WINDOW = 5.0   # 秒；登录后此窗口内到达的断开回调视为旧会话余音

    def __init__(self, gateway):
        super().__init__(gateway)
        self._state = SdkState.IDLE
        self._lock = threading.RLock()
        self._last_login_ts = 0.0   # 最近一次登录成功时刻（余音甄别锚）

    @property
    def state(self) -> SdkState:
        return self._state

    # ——— 生命周期入口（引擎唯一合法路径）———

    def connect(self, userid: str, password: str, client_id: int,
                server_ip: str, server_port: int, quote_protocol: str,
                log_level: int) -> None:
        """建 C 对象 + 心跳 + 首登。成功→LOGGED_IN；登录失败→CREATED（可 relogin）。
        全程持锁（P1）：与 SDK 回调线程的 onDisconnected/login_server 互斥。"""
        with self._lock:
            if self._state not in (SdkState.IDLE, SdkState.DEAD):
                raise SdkLifecycleError(f"connect 在 {self._state.value} 态非法（已连接过）")
            self.userid = userid
            self.password = password
            self.client_id = int(client_id)
            self.server_ip = server_ip
            self.server_port = int(server_port)
            self.protocol = PROTOCOL_VT2XTP[quote_protocol]

            path = get_folder_path(self.gateway_name.lower())
            self.createQuoteApi(self.client_id, str(path).encode("GBK"), log_level)
            self._state = SdkState.CREATED

            # 官方时序窗口（createQuoteApi 之后、login 之前）只存在于这几行之间。
            # 2026-08-25 SEGV 的结构性绝迹点：引擎从任何路径都到不了这里之外的调用位。
            try:
                self.setHeartBeatInterval(self.HEARTBEAT_INTERVAL)
            except Exception as e:
                logger.warning("SDK 心跳设置未生效（SDK 默认值兜底）: %s", e)

            self._login()

    def relogin(self) -> bool:
        """官方重连序列（-2 文档）：LOGGED_IN 先 logout 清场再 login；CREATED 直登。
        返回 login 同步结果（True=已确认）。IDLE/DEAD 抛 SdkLifecycleError。
        全程持锁（P1）：重登序列不与回调驱动的自动重登交叉。"""
        with self._lock:
            if self._state in (SdkState.IDLE, SdkState.DEAD):
                raise SdkLifecycleError(f"relogin 在 {self._state.value} 态非法（C 对象不可用）")
            if self._state is SdkState.LOGGED_IN:
                self._logout_quietly()
            ok = self._login()
            if not ok:
                self._logout_quietly()   # 未确认：给下一轮清服务端会话槽（user already exists）
            return ok

    def close(self) -> None:
        """显式终态落位（双盲审 P2）：DEAD 后 relogin/login_server 必拒（态门已有）。

        连接在（connect_status True，含 LOGGED_IN/半开 CREATED）先尽力 logout 清
        服务端会话槽（_logout_quietly，签名跨版本兜底）；否则直接 DEAD。
        幂等：DEAD 态重复 close no-op。"""
        with self._lock:
            if self._state is SdkState.DEAD:
                return
            if self.connect_status:
                self._logout_quietly()
            self._state = SdkState.DEAD

    # ——— SDK 回调线程安全面（永不抛）———

    def login_server(self) -> bool:
        """覆写：父类 onDisconnected 在 SDK 线程调用本方法——任何异常都炸回调线程。
        非法态 no-op 返回 False；合法态执行登录并返回同步结果。
        全程持锁（P1）：与引擎线程的 connect/relogin 互斥（onDisconnected 持锁再入，
        RLock 同线程可重入）。
        双盲审 P2：_login 全体 try 包——登录路径任何异常（如 query_contract 边界
        错误）仅 warning 返回 False，回调线程「永不抛」是结构保障不只靠态门。"""
        with self._lock:
            if self._state in (SdkState.IDLE, SdkState.DEAD):
                logger.warning("login_server 在 %s 态被拒（C 对象不可用）", self._state.value)
                return False
            try:
                return self._login()
            except Exception as e:
                logger.warning("login_server 登录路径异常（回调线程兜底不抛）: %s", e)
                return False

    def onDisconnected(self, reason: int) -> None:
        """断开回调（SDK 线程，持锁）：旧会话余音甄别后走父类单次自动重登。

        双盲审 P1：relogin 的 logout 会触发旧会话的本回调，若迟到于新 login 成功
        （现态 LOGGED_IN 且距上次登录 <5s），父类自动重登会把新鲜会话再 logout
        （churn 实锤）——判为余音跳过父类只记日志；否则正常走父类（转 CREATED +
        置标志 False + 单次重登，重登经虚分派到本类覆写的 login_server，永不抛）。
        """
        with self._lock:
            if self._last_login_ts and self._state is SdkState.LOGGED_IN:
                age = time.time() - self._last_login_ts
                if age < self.DISCONNECT_ECHO_WINDOW:
                    logger.info("忽略旧会话断开余音（reason=%s，登录后 %.2fs，新会话健康）",
                                reason, age)
                    return
            logger.info("MD 连接断开回调（reason=%s），状态转 CREATED", reason)
            self._state = SdkState.CREATED
            super().onDisconnected(reason)

    def subscribe(self, req) -> None:
        """软防护：非 LOGGED_IN 态 no-op+debug（重放场景需要，勿抛）。

        有意无锁（双盲审 P2）：_state 裸读存在竞态——最坏读到旧值多/漏一次幂等
        订阅（重放周期会补齐），换取调用方永不被引擎线程的重登序列阻塞；无害取舍。
        """
        if self._state is not SdkState.LOGGED_IN:
            logger.debug("subscribe 在 %s 态跳过（%s）", self._state.value, req.symbol)
            return
        super().subscribe(req)

    # ——— 内部 ———

    def _login(self) -> bool:
        """登录并返回同步结果（quote login 0=成功）。

        状态卫生：先把 connect_status/login_status 归 False——vnpy 失败路径不清标志，
        陈旧 True 会把下一次失败误判为成功（2026-08-25 诊断发现）。
        """
        self.connect_status = False
        self.login_status = False
        n = self.login(self.server_ip, self.server_port, self.userid,
                       self.password, self.protocol, "")
        if not n:
            self.connect_status = True
            self.login_status = True
            self._state = SdkState.LOGGED_IN
            self._last_login_ts = time.time()   # 余音甄别锚（P1）：登录成功时刻
            self.gateway.write_log("行情服务器登录成功")
            self.query_contract()
            self.init()
            return True
        error = self.getApiLastError()
        msg = (error or {}).get("error_msg", "")
        self.gateway.write_log(f"行情服务器登录失败，原因：{msg}")
        return False

    def _logout_quietly(self) -> None:
        """尽力优雅登出：半开连接直接 login 必 EISCONN（OS:106）+ 服务端
        "user already exists"——logout 通知服务端释放会话槽再重登（-2 官方序列）。
        签名跨版本不稳（部分带 session 参数），失败不致命，状态必归位。"""
        try:
            try:
                self.logout()
            except TypeError:
                self.logout(0)   # 旧版签名带 session 参数
        except Exception as e:
            logger.debug("MD logout 清场未生效: %s", e)
        finally:
            self.connect_status = False
            self.login_status = False
            self._state = SdkState.CREATED
