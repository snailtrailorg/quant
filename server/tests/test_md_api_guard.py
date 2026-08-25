"""GuardedXtpMdApi 状态机矩阵测试（批 1，2026-08-25 SEGV 事故的回归锁）。

策略：真实例化 GuardedXtpMdApi（Python 子类有 __dict__，实例级打桩可遮蔽继承的
C 绑定方法），绝不触网、不建真 C 会话——构造安全，SEGV 的前提是「构造前调方法」，
那正是状态机要拦的。测试本身如果 SEGV，就是守卫失效的直接证据。
"""
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

    def test_on_disconnected_relogin_via_override(self):
        """onDisconnected（SDK 线程）→ 状态 CREATED → 父类单次重登走本类覆写。"""
        md, calls = _connected(login_result=0)
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
