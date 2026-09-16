"""批27-4：飞书 ws 卡片回调通道（CARD 帧条件改写 patch + card.action.trigger handler；批29-3 起五闸）。

盲审实测三坑全收（v3.1 ⑪）：Frame required 字段补齐（service/SeqID/LogID，漏则
SerializeToString EncodeError）；orig 方法尾部 ACK 依赖 _write_message（测试外层不 stub
会抛 ConnectionClosedException 且在 try 外）；项目无 pytest-asyncio——asyncio.new_event_loop()
run_until_complete 先例（test_eventbus.py）。
"""
import asyncio
import json
import time
from unittest.mock import patch, MagicMock

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()   # 先例 test_eventbus.py：不留 selector fd


def _card_payload(event_type="card.action.trigger", with_event=True):
    body = {"schema": "2.0", "header": {"event_type": event_type, "event_id": "e-1"}}
    if with_event:
        body["event"] = {"operator": {"open_id": "ou_x"}, "action": {"value": {}}}
    return json.dumps(body).encode()


def _frame(type_="card", payload=b"", sum_=1, seq=0, msg_id="m-1"):
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame
    f = Frame()
    for k, v in (("type", type_), ("message_id", msg_id), ("sum", str(sum_)),
                 ("seq", str(seq)), ("trace_id", "t-1")):
        h = f.headers.add()
        h.key, h.value = k, v
    f.payload = payload
    f.service = 1   # required：漏则 SerializeToString 抛 EncodeError（盲审 B 实测）
    f.method = 1   # required（DATA 帧）：端到端链尾部 ACK Serialize 同验
    f.SeqID = 0
    f.LogID = 0
    return f


def _mock_client():
    client = MagicMock()
    client._event_handler._callback_processor_map = {"p2.card.action.trigger": object()}
    client._event_handler._processorMap = {}
    seen = []

    async def orig(frame):
        seen.append({h.key: h.value for h in frame.headers})

    client._handle_data_frame = orig
    client._seen = seen
    return client


def _headers_of(frame):
    return {h.key: h.value for h in frame.headers}


# ——— patch_ws_card_frames ———

def test_patch_rewrites_matching_card_frame():
    """命中注册表（p2. 前缀）的 CARD 帧 → 帧头原地改 event 交原方法（裸键恒不命中=双盲审 P0）。"""
    from src.feishu_bot.ws_client import patch_ws_card_frames
    client = _mock_client()
    with patch("src.feishu_bot.ws_client._sdk_version", return_value="1.7.1"):
        assert patch_ws_card_frames(client) is True
    f = _frame(payload=_card_payload())
    _run(client._handle_data_frame(f))
    assert client._seen[0]["type"] == "event"   # 原方法读到改写后的值


def test_patch_skips_unregistered_event_type():
    from src.feishu_bot.ws_client import patch_ws_card_frames
    client = _mock_client()
    with patch("src.feishu_bot.ws_client._sdk_version", return_value="1.7.1"):
        patch_ws_card_frames(client)
    f = _frame(payload=_card_payload(event_type="unknown.event"))
    _run(client._handle_data_frame(f))
    assert client._seen[0]["type"] == "card"   # 未命中不改写，维持 SDK 原行为


def test_patch_skips_event_frame():
    from src.feishu_bot.ws_client import patch_ws_card_frames
    client = _mock_client()
    with patch("src.feishu_bot.ws_client._sdk_version", return_value="1.7.1"):
        patch_ws_card_frames(client)
    f = _frame(type_="event", payload=_card_payload())
    _run(client._handle_data_frame(f))
    assert client._seen[0]["type"] == "event"   # EVENT 帧直通不改


def test_patch_corrupt_payload_passthrough():
    """peek 全包容错：坏 payload 不抛异常（wrap 抛=整帧被 _handle_message 吞掉）且不改写。"""
    from src.feishu_bot.ws_client import patch_ws_card_frames
    client = _mock_client()
    with patch("src.feishu_bot.ws_client._sdk_version", return_value="1.7.1"):
        patch_ws_card_frames(client)
    f = _frame(payload=b"\xff not json")
    _run(client._handle_data_frame(f))
    assert client._seen[0]["type"] == "card"


def test_patch_unverified_sdk_not_installed():
    from src.feishu_bot.ws_client import patch_ws_card_frames
    client = _mock_client()
    orig = client._handle_data_frame
    with patch("src.feishu_bot.ws_client._sdk_version", return_value="9.9.9"):
        assert patch_ws_card_frames(client) is False
    assert client._handle_data_frame is orig   # 未替换


def test_patch_multipart_card_frame_peeks_combined():
    """分包 CARD：wrapper 先 _combine 合成再 peek（None=未集齐透传；合成后命中改写）。"""
    from src.feishu_bot.ws_client import patch_ws_card_frames
    client = _mock_client()
    full = _card_payload()
    client._combine = MagicMock(side_effect=[None, full])   # 首帧未集齐 → 次帧合成
    with patch("src.feishu_bot.ws_client._sdk_version", return_value="1.7.1"):
        patch_ws_card_frames(client)
    f1 = _frame(payload=b"frag", sum_=2, seq=0)
    _run(client._handle_data_frame(f1))
    assert client._seen[0]["type"] == "card"   # 未集齐不改写
    f2 = _frame(payload=b"ment", sum_=2, seq=1)
    _run(client._handle_data_frame(f2))
    assert client._seen[1]["type"] == "event"   # 合成后命中改写


def test_sdk_combine_last_frame_idempotent():
    """钉住 SDK _combine 末帧重入幂等（wrapper 与原方法各调一次的前提，防升级漂移）。"""
    import lark_oapi as lark
    client = lark.ws.Client(app_id="x", app_secret="y")
    assert client._combine("m", 2, 0, b"half-") is None
    assert client._combine("m", 2, 1, b"full") == b"half-full"
    assert client._combine("m", 2, 1, b"full") == b"half-full"   # 重入幂等


# ——— _card_gates 五闸（批29-3：平台级闸退役）———

def _gates_env(event_id="e-1", ts=None, perms=("halt",), identity=True):
    """闸门公共 mock：identity/redis/execute 全 mock，返回收集器。"""
    value = {"action": "confirm", "tool": "emergency_halt", "args": {"id": "s1"},
             "ts": ts if ts is not None else int(time.time())}
    ident = {"username": "alice", "perms": set(perms)} if identity else None
    fake_redis = MagicMock()
    fake_redis.set = MagicMock(side_effect=lambda k, *a, **kw: True)   # 默认全放行
    exec_calls = []
    texts = []
    fc = MagicMock()
    fc.send_text = MagicMock(side_effect=lambda rid, t, *a, **kw: texts.append(t))
    return value, ident, fake_redis, exec_calls, texts, fc


def _run_gates(value, ident, fake_redis, exec_calls, texts, fc, mid="", event_id="e-1"):
    from src.feishu_bot import ws_client
    with patch("redis.Redis.from_url", return_value=fake_redis), \
         patch("src.im_bot.users.resolve_im_identity", return_value=ident), \
         patch("src.im_bot.feishu_client.execute_confirmed_tool",
               side_effect=lambda *a, **kw: exec_calls.append(a)), \
         patch("src.im_bot.feishu_client.get_feishu_client", return_value=fc):
        ws_client._card_gates(event_id, value, "ou_x", 7, mid)


def test_gates_pass_executes_with_username():
    v, ident, r, calls, texts, fc = _gates_env()
    _run_gates(v, ident, r, calls, texts, fc)
    assert len(calls) == 1
    assert calls[0][0] == "ou_x" and calls[0][1] == "emergency_halt"
    assert json.loads(calls[0][2]) == {"id": "s1"}
    assert calls[0][3] == "alice"   # 批27-13：username 显式传


def test_gates_stale_rejected():
    v, ident, r, calls, texts, fc = _gates_env(ts=int(time.time()) - 120)
    _run_gates(v, ident, r, calls, texts, fc)
    assert calls == []


def test_gates_cancel_ignored():
    """cancel 仅 ACK 不操作（对齐 HTTP 面）——闸门零消耗直接 return。"""
    v, ident, r, calls, texts, fc = _gates_env()
    v["action"] = "cancel"
    _run_gates(v, ident, r, calls, texts, fc)
    assert calls == [] and r.set.call_count == 0


def test_gates_identity_missing_rejected():
    v, ident, r, calls, texts, fc = _gates_env(identity=False)
    _run_gates(v, ident, r, calls, texts, fc)
    assert calls == []


def test_gates_perm_denied():
    v, ident, r, calls, texts, fc = _gates_env(perms=("read",))   # halt 不在 perms
    _run_gates(v, ident, r, calls, texts, fc)
    assert calls == []


def test_gates_event_id_dup_dropped():
    v, ident, r, calls, texts, fc = _gates_env()
    r.set = MagicMock(return_value=False)   # 两键全 False=重推
    _run_gates(v, ident, r, calls, texts, fc)
    assert calls == []


def test_gates_exec_dup_dropped():
    """同卡连点：event_id 键放行、exec 键拦截（每次点击新 event_id，⑤挡不住 ⑥兜住）。"""
    v, ident, r, calls, texts, fc = _gates_env()
    r.set = MagicMock(side_effect=lambda k, *a, **kw: not k.startswith("feishu:card:exec:"))
    _run_gates(v, ident, r, calls, texts, fc)
    assert calls == []
    assert texts and "已经执行过" in texts[0]   # 拦截有用户反馈（代码盲审：静默吞=交易操作零反馈高危）


def test_gates_exec_key_distinguishes_cards():
    """exec 键以 mid 为主成分：同秒同 tool 的两张不同卡（"停掉 s1 和 s2"场景）各自执行，
    不互斥（代码盲审 A/B 共识——纯 {ts}:{tool} 会静默吞第二张）。"""
    r = MagicMock()
    r.set = MagicMock(return_value=True)
    seen_keys = []
    r.set.side_effect = lambda k, *a, **kw: seen_keys.append(k) or True

    def run(mid, event_id):
        calls, texts, fc = [], [], MagicMock()
        v, ident, *_ = _gates_env()
        v["args"] = {"id": "s2"} if mid == "om_b" else {"id": "s1"}
        _run_gates(v, ident, r, calls, texts, fc, mid=mid, event_id=event_id)
        return calls

    c1 = run("om_a", "e-1")
    c2 = run("om_b", "e-2")
    assert len(c1) == 1 and json.loads(c1[0][2]) == {"id": "s1"}
    assert len(c2) == 1 and json.loads(c2[0][2]) == {"id": "s2"}
    exec_keys = [k for k in seen_keys if k.startswith("feishu:card:exec:")]
    assert len(set(exec_keys)) == 2   # 两卡两键


def test_gates_exec_key_fallback_without_mid():
    """mid 缺失回退 ts:open_id——不同人同秒同 tool 仍可分。"""
    v, ident, r, calls, texts, fc = _gates_env()
    keys = []
    r.set = MagicMock(side_effect=lambda k, *a, **kw: keys.append(k) or True)
    _run_gates(v, ident, r, calls, texts, fc, mid="")
    assert any("ou_x" in k for k in keys if k.startswith("feishu:card:exec:"))


# ——— 端到端：真 SDK Client + 真 dispatcher 派发（防同版本热改属性名，代码盲审 A-P2）———

def test_patch_end_to_end_real_sdk_dispatch():
    """真 lark.ws.Client + 真 EventDispatcherHandler：CARD 帧（真 protobuf）→ patch 改写 →
    on_card 真被调（patch_ws_card_frames 读的注册表属性名/键格式对真 SDK 全链生效）。"""
    import lark_oapi as lark
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from src.feishu_bot.ws_client import patch_ws_card_frames
    got = []

    def on_card(data):
        got.append((getattr(getattr(data, "header", None), "event_id", ""),
                    getattr(getattr(getattr(data, "event", None), "operator", None), "open_id", "")))

    handler = (EventDispatcherHandler.builder("", "")
               .register_p2_card_action_trigger(on_card).build())
    client = lark.ws.Client(app_id="x", app_secret="y", event_handler=handler)

    async def _stub_write(data):
        pass
    client._write_message = _stub_write   # 未连接时 EVENT 尾部 ACK 会抛且在原方法 try 外

    with patch("src.feishu_bot.ws_client._sdk_version", return_value="1.7.1"):
        assert patch_ws_card_frames(client) is True
    f = _frame(payload=_card_payload())
    _run(client._handle_data_frame(f))
    assert got == [("e-1", "ou_x")]   # 端到端：改写→_do_without_validation→callback processor→on_card


def test_gates_redis_failure_fail_open():
    """redis 故障放行（对齐 HTTP 面 fail-open+warning）。"""
    v, ident, r, calls, texts, fc = _gates_env()
    r.set = MagicMock(side_effect=Exception("redis down"))
    _run_gates(v, ident, r, calls, texts, fc)
    assert len(calls) == 1


# ——— make_card_handler：字段提取+spawn ———

class _SyncThread:
    """Thread 替身：start 即 join（handler spawn 是异步的，断言前须等闸门跑完）。
    真类在类体外先取——patch 之后再取 threading.Thread 会拿到替身自身=无限递归。"""
    _REAL = None

    def __init__(self, *a, **kw):
        self._t = type(self)._REAL(*a, **kw)

    def start(self):
        self._t.start()
        self._t.join(timeout=5)


import threading as _threading   # noqa: E402
_SyncThread._REAL = _threading.Thread


def test_card_handler_extracts_and_spawns():
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger
    from src.feishu_bot import ws_client
    data = P2CardActionTrigger({
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "event_id": "e-9"},
        "event": {"operator": {"open_id": "ou_9"},
                  "action": {"value": {"action": "confirm", "tool": "risk_resume", "ts": 1}}}})
    gate_calls = []
    with patch.object(ws_client, "_card_gates", side_effect=lambda *a: gate_calls.append(a)), \
         patch("threading.Thread", _SyncThread):
        ws_client.make_card_handler(3)(data)
    assert gate_calls == [("e-9", {"action": "confirm", "tool": "risk_resume", "ts": 1},
                           "ou_9", 3, "")]   # mid 缺省 ""


def test_card_handler_bad_data_no_raise():
    """字段提取全容错：无任何属性的裸对象 → 空值安全提取+spawn，不抛异常。"""
    from src.feishu_bot import ws_client
    with patch.object(ws_client, "_card_gates") as g, patch("threading.Thread", _SyncThread):
        ws_client.make_card_handler(3)(object())
        assert g.called
        assert g.call_args[0] == ("", {}, "", 3, "")


# ——— 2.0 卡结构与降级链 ———

def test_card_2_0_structure():
    from src.im_bot.feishu_client import build_confirm_card
    card = build_confirm_card("strategy_stop", {"id": "s1"}, reason="r")
    assert card["schema"] == "2.0"
    assert "action" not in card   # 顶层无 action 键（盲审 A-P0-2：按钮直挂 body.elements）
    els = card["body"]["elements"]
    assert els[0]["tag"] == "markdown"
    ok, cancel = els[1], els[2]
    assert ok["behaviors"][0]["type"] == "callback"
    assert ok["behaviors"][0]["value"]["action"] == "confirm"
    assert ok["behaviors"][0]["value"]["tool"] == "strategy_stop"
    assert ok["behaviors"][0]["value"]["args"] == {"id": "s1"}
    assert isinstance(ok["behaviors"][0]["value"]["ts"], int)
    assert cancel["behaviors"][0]["value"]["action"] == "cancel"
    assert "args" not in cancel["behaviors"][0]["value"]


def test_confirm_card_degrades_when_channel_off():
    """CARD_CHANNEL_OK=False → confirm_card 降级文本（不发点了没反应的卡）。"""
    from src.im_bot import feishu_client as fc
    fc2 = MagicMock()
    captured = {}

    def fake_incoming(provider, fid, uid, text, reply, chat_type, *, confirm_card=None):
        captured["cc"] = confirm_card

    with patch.object(fc, "get_feishu_client", return_value=fc2), \
         patch("src.im_bot.handlers.handle_incoming", side_effect=fake_incoming), \
         patch.object(fc, "CARD_CHANNEL_OK", False):
        fc.process_message_async("ou_1", "hi", "open_id", "ou_1", 5, "p2p")
        captured["cc"]("emergency_halt", {})   # 须在 patch 作用域内调用（flag 读模块全局）
    assert fc2.send_text.called and not fc2.send_card.called
    assert "没有执行" in fc2.send_text.call_args[0][1]


def test_confirm_card_normal_when_channel_on():
    from src.im_bot import feishu_client as fc
    fc2 = MagicMock()
    captured = {}

    def fake_incoming(provider, fid, uid, text, reply, chat_type, *, confirm_card=None):
        captured["cc"] = confirm_card

    with patch.object(fc, "get_feishu_client", return_value=fc2), \
         patch("src.im_bot.handlers.handle_incoming", side_effect=fake_incoming):
        fc.process_message_async("ou_1", "hi", "open_id", "ou_1", 5, "p2p")
        captured["cc"]("emergency_halt", {})
    assert fc2.send_card.called and not fc2.send_text.called


def test_main_probe_before_start():
    """main() 启动链冒烟（快审 P0 教训：函数内 import 时序=UnboundLocalError 启动即崩，
    常规测试不碰 main() 抓不到——启动级 bug 必须钉）。批29 改写：CARD_SELF_BOT 断言随
    flag 退役删除，保留"探针置位先于 client.start()"断言（批29-3 恰再动 main() 函数体）。"""
    import sys
    from src.feishu_bot import ws_client
    from src.im_bot import feishu_client as fc
    order = []

    fake_client = MagicMock()
    fake_client.start = MagicMock(side_effect=lambda: order.append("start"))
    saved_fid = ws_client._FID
    saved_ok = fc.CARD_CHANNEL_OK
    try:
        with patch.object(sys, "argv", ["ws_client", "7"]), \
             patch("src.im_bot.users.backfill_from_env"), \
             patch("src.feishu_bot.ws_client.load_feishu_credentials", return_value=("ai", "sk")), \
             patch("lark_oapi.ws.Client", return_value=fake_client), \
             patch("src.feishu_bot.ws_client.patch_ws_card_frames",
                   side_effect=lambda c: order.append("patch") or True):
            ws_client.main()
            assert order == ["patch", "start"]   # 探针先于连接启动（finally 恢复前断言）
    finally:
        ws_client._FID = saved_fid
        fc.CARD_CHANNEL_OK = saved_ok


# ——— 批29：卡片面用户化新钉 ———

def test_handle_incoming_passes_full_toolset():
    """批29-1（P0 回归钉）：gateway.chat 须传 tools=None——批13 起 tools=READ_TOOLS 与
    _filter_tools 交集规则叠加把操作工具全滤掉（27-4 真机测试根因）。原测试 mock 网关
    从不断言 tools 参数=恒绿盲区，此钉补上。"""
    from types import SimpleNamespace
    from src.im_bot import handlers
    gw = MagicMock()
    gw.chat = MagicMock(return_value=SimpleNamespace(tool_calls=[], content="ok"))
    ident = {"user_id": 1, "username": "alice", "role": "admin",
             "perms": {"read", "strategy_control", "halt", "trade", "resume"}}
    replied = []
    with patch("src.llm_gateway.gateway", gw), \
         patch("src.im_bot.users.resolve_im_identity", return_value=ident):
        handlers.handle_incoming("feishu", 7, "ou_x", "急停", lambda t: replied.append(t), "p2p")
    assert gw.chat.called
    assert gw.chat.call_args.kwargs.get("tools") is None   # None=纯 perms 档位（操作工具可见）
    assert replied == ["ok"]


def test_gates_owner_bot_executes_with_fid():
    """批29：自助 bot 正向闸门（平台级闸退役后，五闸=时效→身份→权限→dedup→exec）+
    fid 透传钉（29-2b：回执走本 bot 凭证——原查 owner IS NULL 恒空回落错 bot）。"""
    from src.feishu_bot import ws_client
    v = {"action": "confirm", "tool": "emergency_halt", "args": {"id": "s1"}, "ts": int(time.time())}
    ident = {"username": "alice", "perms": {"read", "trade", "halt", "resume"}}
    fake_redis = MagicMock()
    fake_redis.set = MagicMock(return_value=True)
    captured = []
    fc = MagicMock()
    with patch("redis.Redis.from_url", return_value=fake_redis), \
         patch("src.im_bot.users.resolve_im_identity", return_value=ident), \
         patch("src.im_bot.feishu_client.execute_confirmed_tool",
               side_effect=lambda *a, **kw: captured.append((a, kw))), \
         patch("src.im_bot.feishu_client.get_feishu_client", return_value=fc):
        ws_client._card_gates("e-1", v, "ou_x", 7, "om_1")
    assert len(captured) == 1
    assert captured[0][0][1] == "emergency_halt"
    assert captured[0][1].get("fid") == 7   # 批29-2b：回执 per-bot
