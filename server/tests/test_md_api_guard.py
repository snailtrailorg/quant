"""GuardedXtpMdApi 状态机矩阵测试（批 1，2026-08-25 SEGV 事故的回归锁）。

策略：真实例化 GuardedXtpMdApi（Python 子类有 __dict__，实例级打桩可遮蔽继承的
C 绑定方法），绝不触网、不建真 C 会话——构造安全，SEGV 的前提是「构造前调方法」，
那正是状态机要拦的。测试本身如果 SEGV，就是守卫失效的直接证据。

批 2 增补（双盲审 P1）：RLock 互斥 + 旧会话断开余音甄别（onDisconnected 在
登录成功后 5s 窗口内到达且现态 LOGGED_IN → 跳过父类自动重登防 churn）。
"""
import threading
import time
from types import SimpleNamespace

import pytest

from src.strategy_framework.md_api_guard import GuardedXtpMdApi, SdkLifecycleError, SdkState


class _FakeGateway:
    def __init__(self):
        self.gateway_name = "XTP"
        self.logs = []

    def write_log(self, msg):
        self.logs.append(str(msg))


def _stubbed(login_result: int = 0):
    """实例级打桩 C 面：记录调用序，login 返回注入结果（0=成功）。"""
    md = GuardedXtpMdApi(_FakeGateway())
    calls = []
    md.createQuoteApi = lambda *a: calls.append(["createQuoteApi"])
    md.setHeartBeatInterval = lambda n: calls.append(["heartbeat", n])
    md._real_login = md.login
    md.login = lambda *a: (calls.append(["login"]), login_result)[1]
    md.getApiLastError = lambda: {"error_id": 1, "error_msg": "stub-err"}
    md.query_contract = lambda: calls.append(["query_contract"])
    md.init = lambda: calls.append(["init"])
    md.logout = lambda *a: calls.append(["logout", a])
    return md, calls


def _connected(login_result: int = 0):
    md, calls = _stubbed(login_result)
    md.connect("u", "p", 1, "127.0.0.1", 6002, "TCP", 1)
    return md, calls


SETTINGS = ("u", "p", 1, "127.0.0.1", 6002, "TCP", 1)


class TestConnectSequence:
    def test_heartbeat_between_create_and_login(self):
        """SEGV 回归锁：心跳必须严格位于 createQuoteApi 之后、login 之前。"""
        md, calls = _connected(login_result=0)
        names = [c[0] for c in calls]
        assert names.index("createQuoteApi") < names.index("heartbeat") < names.index("login")
        assert calls[names.index("heartbeat")][1] == 15
        assert md.state is SdkState.LOGGED_IN
        assert md.login_status is True

    def test_login_fail_stays_created(self):
        """首登失败：状态 CREATED（可 relogin 续），标志卫生为 False。"""
        md, calls = _connected(login_result=1)
        assert md.state is SdkState.CREATED
        assert md.login_status is False and md.connect_status is False
        assert any("登录失败" in s and "stub-err" in s for s in md.gateway.logs)

    def test_double_connect_raises(self):
        """重复 connect 抛 SdkLifecycleError（不触 C）。"""
        md, _ = _connected()
        with pytest.raises(SdkLifecycleError):
            md.connect(*SETTINGS)

    def test_heartbeat_failure_tolerated(self):
        """心跳方法异常（版本签名变化）：警告兜底，连接继续。"""
        md, calls = _stubbed(login_result=0)
        md.setHeartBeatInterval = lambda n: (_ for _ in ()).throw(TypeError("sig"))
        md.connect(*SETTINGS)
        assert md.state is SdkState.LOGGED_IN


class TestRelogin:
    def test_from_logged_in_logout_first(self):
        """官方 -2 序列：LOGGED_IN 态先 logout 清场再 login。"""
        md, calls = _connected(login_result=0)
        calls.clear()
        assert md.relogin() is True
        names = [c[0] for c in calls]
        assert names.index("logout") < names.index("login")
        assert md.state is SdkState.LOGGED_IN

    def test_from_created_direct_login(self):
        """socket 已死态（CREATED，hub 日切场景）：直登不 logout。"""
        md, calls = _connected(login_result=1)   # 首登失败 -> CREATED
        md.login = lambda *a: (calls.append(["login"]), 0)[1]   # 本轮恢复
        calls.clear()
        assert md.relogin() is True
        assert [c[0] for c in calls] == ["login", "query_contract", "init"]

    def test_failure_cleans_slot(self):
        """relogin 未确认（服务端槽占用）：login 后补一发 logout 清槽，状态回 CREATED。"""
        md, calls = _connected(login_result=0)
        md.login = lambda *a: (calls.append(["login"]), 1)[1]   # 本轮服务端拒绝
        calls.clear()
        assert md.relogin() is False
        names = [c[0] for c in calls]
        assert names == ["logout", "login", "logout"]   # 清场->登录->失败补清
        assert md.state is SdkState.CREATED

    def test_idle_raises_without_c_touch(self):
        """IDLE 态 relogin 抛 SdkLifecycleError，零 C 调用（构造前触达=SEGV 的拦截证明）。"""
        md, calls = _stubbed()
        with pytest.raises(SdkLifecycleError):
            md.relogin()
        assert calls == []


class TestCallbackThreadSafety:
    def test_login_server_idle_never_raises(self):
        """SDK 回调线程面：IDLE 态 no-op 返回 False，不抛不触 C。"""
        md, calls = _stubbed()
        assert md.login_server() is False
        assert calls == []

    def test_login_server_login_path_exception_swallowed(self):
        """双盲审 P2：_login 内任何异常（query_contract 抛错）——login_server
        不抛、返回 False（回调线程「永不抛」的结构性保障，不只靠态门）。"""
        md, calls = _connected(login_result=0)
        md.query_contract = lambda: (_ for _ in ()).throw(RuntimeError("sdk edge"))
        assert md.login_server() is False   # 不抛即通过

    def test_on_disconnected_relogin_via_override(self):
        """onDisconnected（SDK 线程，超余音窗）→ 状态 CREATED → 父类单次重登走本类覆写。"""
        md, calls = _connected(login_result=0)
        md._last_login_ts = time.time() - 10      # 老化锚点：越过 5s 余音窗
        calls.clear()
        md.onDisconnected(0)
        assert md.state is SdkState.LOGGED_IN
        assert any(c[0] == "login" for c in calls)
        assert not any(isinstance(c, Exception) for c in calls)

    def test_stale_login_status_hygiene(self):
        """状态卫生：陈旧 login_status=True 不会把失败登录误判成功。"""
        md, _ = _stubbed(login_result=1)
        md.login_status = True   # 模拟上一会话残留
        md.connect(*SETTINGS)
        assert md.login_status is False


class TestSubscribeGuard:
    def _req(self):
        from vnpy.trader.constant import Exchange
        return SimpleNamespace(symbol="600000", exchange=Exchange.SSE)

    def test_not_logged_in_noop(self):
        """非 LOGGED_IN 态订阅 no-op（重放场景软防护）。"""
        md, calls = _stubbed()
        sm_calls = []
        md.subscribeMarketData = lambda *a: sm_calls.append(a)
        md.subscribe(self._req())
        assert sm_calls == []

    def test_logged_in_passes_through(self):
        """LOGGED_IN 态正常走父类订阅路径。"""
        md, calls = _connected(login_result=0)
        sm_calls = []
        md.subscribeMarketData = lambda *a: sm_calls.append(a)
        md.subscribe(self._req())
        assert len(sm_calls) == 1


class TestCloseDeadState:
    """close() 落位 DEAD（双盲审 P2）：连接在先 logout 清服务端会话槽；
    DEAD 后 relogin/login_server 必拒（态门已有）；幂等。"""

    def test_close_logged_in_logout_then_dead(self):
        """LOGGED_IN 态 close：先 logout（清会话槽）再转 DEAD。"""
        md, calls = _connected(login_result=0)
        calls.clear()
        md.close()
        assert md.state is SdkState.DEAD
        assert [c[0] for c in calls] == ["logout"]

    def test_close_disconnected_direct_dead(self):
        """未连接（connect_status False）close：直接 DEAD，零 C 调用。"""
        md, calls = _connected(login_result=1)   # 首登失败 -> CREATED，未连
        calls.clear()
        md.close()
        assert md.state is SdkState.DEAD
        assert calls == []

    def test_dead_rejects_relogin_and_login_server(self):
        """DEAD 后 relogin 抛 SdkLifecycleError / login_server 返回 False。"""
        md, calls = _connected(login_result=0)
        md.close()
        calls.clear()
        with pytest.raises(SdkLifecycleError):
            md.relogin()
        assert md.login_server() is False
        assert calls == []   # 两条路径均零 C 调用

    def test_close_idempotent(self):
        """DEAD 态重复 close no-op（不再触 logout）。"""
        md, calls = _connected(login_result=0)
        md.close()
        calls.clear()
        md.close()
        assert calls == [] and md.state is SdkState.DEAD


class TestLogoutSignature:
    def test_legacy_signature_fallback(self):
        """logout() 无参 TypeError -> logout(0) 旧签名兜底。"""
        md, _ = _connected(login_result=0)
        got = []

        def _lo(*args):
            if not args:
                raise TypeError("logout() missing 1 required positional argument")
            got.append(args[0])
        md.logout = _lo
        assert md.relogin() is True
        assert got == [0]


class TestDisconnectEcho:
    """旧会话余音甄别（双盲审 P1）：relogin 的 logout 触发旧会话断开回调，
    迟到于新 login 成功时若走父类自动重登，新鲜会话将被再 logout（churn 实锤）。"""

    def test_echo_right_after_fresh_relogin_skipped(self):
        """刚 relogin 成功即收到 onDisconnected：跳过父类重登，login 不再被调。"""
        md, calls = _connected(login_result=0)
        assert md.relogin() is True                # 新 login 成功 -> _last_login_ts 刷新
        calls.clear()
        md.onDisconnected(0)                       # 旧会话余音（<5s 到达）
        assert not any(c[0] == "login" for c in calls)
        assert md.state is SdkState.LOGGED_IN      # 新会话未被搅动

    def test_echo_after_fresh_connect_skipped(self):
        """首登成功后立刻断开回调（同窗语义）：同样不触发父类重登。"""
        md, calls = _connected(login_result=0)
        assert md._last_login_ts > 0               # 登录成功记锚（余音甄别依据）
        calls.clear()
        md.onDisconnected(0)
        assert not any(c[0] == "login" for c in calls)
        assert md.state is SdkState.LOGGED_IN

    def test_stale_beyond_window_goes_parent(self):
        """超 5s 窗口的断开：真断链，照走父类（转 CREATED + 单次自动重登）。"""
        md, calls = _connected(login_result=0)
        md._last_login_ts = time.time() - 10
        calls.clear()
        md.onDisconnected(0)
        assert any(c[0] == "login" for c in calls)
        assert md.state is SdkState.LOGGED_IN

    def test_created_state_goes_parent(self):
        """CREATED 态 onDisconnected 照走父类（余音判据要求 LOGGED_IN）。"""
        md, calls = _connected(login_result=1)     # 首登失败 -> CREATED
        md.login = lambda *a: (calls.append(["login"]), 0)[1]   # 本轮恢复
        calls.clear()
        md.onDisconnected(0)
        assert any(c[0] == "login" for c in calls)
        assert md.state is SdkState.LOGGED_IN


class TestLocking:
    """RLock 互斥（双盲审 P1）：引擎面（connect/relogin）与 SDK 回调面
    （onDisconnected/login_server）全程持锁，重登序列不与回调交叉。"""

    def test_relogin_holds_lock_through_login(self):
        """互斥证据：relogin 全程持锁，慢 login 期间其他线程拿不到锁。"""
        md, calls = _connected(login_result=0)
        in_login = threading.Event()
        release = threading.Event()

        def _slow_login(*a):
            in_login.set()
            release.wait(5)
            return 0
        md.login = _slow_login
        t = threading.Thread(target=md.relogin, daemon=True)
        t.start()
        assert in_login.wait(5)                    # 已进入锁内 login
        got = md._lock.acquire(timeout=0.3)
        if got:
            md._lock.release()
        assert got is False                        # 锁仍被 relogin 持有
        release.set()
        t.join(5)
        assert md.state is SdkState.LOGGED_IN

    def test_on_disconnected_during_relogin_queues_then_echo_skipped(self):
        """端到端：relogin 持锁期间 SDK 线程 onDisconnected 排队，锁释放后判余音
        跳过——不 churn、不死锁，全程只此一发 login。"""
        md, calls = _connected(login_result=0)
        in_login = threading.Event()
        release = threading.Event()
        logins = []

        def _slow_login(*a):
            logins.append(1)
            in_login.set()
            release.wait(5)
            return 0
        md.login = _slow_login
        t = threading.Thread(target=md.relogin, daemon=True)
        t.start()
        assert in_login.wait(5)
        d = threading.Thread(target=md.onDisconnected, args=(0,), daemon=True)
        d.start()
        time.sleep(0.2)                            # 排队中（relogin 未放锁）
        release.set()
        t.join(5)
        d.join(5)
        assert not d.is_alive()                    # 未死锁
        assert len(logins) == 1                    # 仅 relogin 那一发；余音未再登
        assert md.state is SdkState.LOGGED_IN

    def test_login_server_holds_lock(self):
        """login_server（回调面）同样全程持锁。"""
        md, calls = _connected(login_result=0)
        in_login = threading.Event()
        release = threading.Event()

        def _slow_login(*a):
            in_login.set()
            release.wait(5)
            return 0
        md.login = _slow_login
        t = threading.Thread(target=md.login_server, daemon=True)
        t.start()
        assert in_login.wait(5)
        got = md._lock.acquire(timeout=0.3)
        if got:
            md._lock.release()
        assert got is False
        release.set()
        t.join(5)
        assert md.state is SdkState.LOGGED_IN
