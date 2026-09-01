"""批 7 · 告警订阅分发单测（docs/任务/批7-告警订阅分发.md mock 18 点）。

策略：_dispatch_async 为纯同步函数直测（executor 线程只包 dispatch() 入口）；
patch _load_channels/_send_*/_get_producer/_writeback/_throttled/_quota_exceeded。
"""
from unittest.mock import MagicMock, patch

import pytest

import importlib
D = importlib.import_module("src.alert_notify.dispatch")
N = importlib.import_module("src.alert_notify.notify")


def _row(ch="im", target="10", cats=None, min_level="warn", enabled=True):
    return {"channel": ch, "target": target, "categories": cats or ["risk"],
            "min_level": min_level, "enabled": enabled}


def _run(level="critical", category="risk", title="t", rows=None, **kw):
    """跑 _dispatch_async，捕获回写与投递。"""
    wb, sent = [], []
    prod = MagicMock()
    prod.send_task.side_effect = lambda *a, **k: sent.append((a, k))
    defaults = dict(
        _load_channels=MagicMock(return_value=rows if rows is not None else [_row()]),
        _writeback=lambda nid, ch, v: wb.append((ch, v)),
        _throttled=MagicMock(return_value=False),
        _quota_exceeded=MagicMock(return_value=False),
        _get_producer=MagicMock(return_value=prod),
    )
    defaults.update(kw)
    import contextlib
    patches = [patch.object(D, "_load_channels", defaults["_load_channels"]),
               patch.object(D, "_writeback", defaults["_writeback"]),
               patch.object(D, "_writeback_empty", MagicMock()),
               patch.object(D, "_get_producer", defaults["_get_producer"])]
    if defaults["_throttled"] is not None:
        patches.append(patch.object(D, "_throttled", defaults["_throttled"]))
    if defaults["_quota_exceeded"] is not None:
        patches.append(patch.object(D, "_quota_exceeded", defaults["_quota_exceeded"]))
    with contextlib.ExitStack() as st:
        for p_ in patches:
            st.enter_context(p_)
        D._dispatch_async(level, category, title, "b", "l3.failed", 42)
    return wb, sent


# ①② 级别门槛（类别过滤见 test_empty_terminal_writeback / payload 断言见 test_enqueue_order_and_payload）
def test_level_gate_info_rejected():
    """info 拒：dispatch() 入口闸（_dispatch_async 无闸——A 评 P2-6 改直测入口）。"""
    with patch.object(D, "_submit") as p_sub, patch.object(D, "_writeback_empty") as p_empty:
        D.dispatch("info", "risk", "t", "b", None, 1)
    p_sub.assert_not_called(); p_empty.assert_not_called()
    # 通道级 min_level 过滤：critical-only 订阅不收 warn
    wb, sent = _run(level="warn", rows=[_row(min_level="critical")])
    assert not sent and not wb


def test_empty_terminal_writeback():
    """③(B3-2) 零匹配 → {} 空终态（≠null 死亡窗）。"""
    called = {}
    with patch.object(D, "_load_channels", return_value=[_row(cats=["risk"])]):
        with patch.object(D, "_writeback_empty", side_effect=lambda nid: called.setdefault("nid", nid)):
            D._dispatch_async("critical", "data", "t", "b", None, 7)
    assert called["nid"] == 7


# ③ 节流 skip
def test_throttle_skips_and_writeback():
    wb, sent = _run(_throttled=MagicMock(return_value=True))
    assert not sent and ("im", "skip:throttled") in wb


# ④ 配额 skip
def test_quota_skips():
    wb, sent = _run(_quota_exceeded=MagicMock(return_value=True))
    assert not sent and ("im", "skip:quota") in wb


# ⑤ fail-open：节流/配额 Valkey 故障 → 放行（容错在函数内部的 except——patch _redis 触发真实路径）
def test_failopen_valkey_down():
    prod = MagicMock(); sent = []
    prod.send_task.side_effect = lambda *a, **k: sent.append((a, k))
    with patch.object(D, "_load_channels", return_value=[_row()]), \
         patch.object(D, "_writeback", lambda n, c, v: None), \
         patch.object(D, "_redis", side_effect=Exception("valkey down")), \
         patch.object(D, "_get_producer", return_value=prod):
        D._dispatch_async("critical", "risk", "t", "b", None, 42)   # 不抛
    assert sent and sent[0][1]["queue"] == "alerts_im"


# ⑥ 先写 queued 再投 + 队列名/任务名/payload 快照
def test_enqueue_order_and_payload():
    wb, sent = _run()
    assert ("im", "queued") in wb
    assert sent, "应投 alerts_im 队列"
    args, kwargs = sent[0]
    assert args[0] == "alerts.send_im"
    assert kwargs["queue"] == "alerts_im"
    assert kwargs["expires"] == 3600
    assert kwargs["kwargs"]["row"]["target"] == "10"     # 行快照
    assert kwargs["kwargs"]["notif_id"] == 42


# ⑥b enqueue 失败 → 降级直发（不重打节流键——节流在投前已打）
def test_degrade_to_direct_send():
    prod = MagicMock()
    prod.send_task.side_effect = Exception("broker down")
    direct = []
    with patch.object(D, "_load_channels", return_value=[_row()]), \
         patch.object(D, "_writeback", lambda n, c, v: direct.append((c, v))), \
         patch.object(D, "_throttled", MagicMock(return_value=False)), \
         patch.object(D, "_quota_exceeded", MagicMock(return_value=False)), \
         patch.object(D, "_get_producer", return_value=prod), \
         patch.object(D, "_send_one", return_value=(True, "ok")) as p_send:
        D._dispatch_async("critical", "risk", "t", "b", None, 42)
    p_send.assert_called_once()                          # 直发恰好一次
    assert ("im", "ok") in direct                        # 降级终态回写


# ⑦ 异常隔离：单通道 sender 抛不反噬其他通道
def test_sender_exception_isolated():
    rows = [_row(ch="im"), _row(ch="email", target="a@b.c", cats=["risk"])]
    wb, sent = _run(rows=rows)
    assert len(sent) == 2                                 # im 抛了 email 照投（_send_one 只在降级路径调用；此处全走队列）
    with patch.object(D, "_load_channels", return_value=rows), \
         patch.object(D, "_writeback", lambda n, c, v: wb.append((c, v))), \
         patch.object(D, "_throttled", MagicMock(return_value=False)), \
         patch.object(D, "_quota_exceeded", MagicMock(return_value=False)):
        prod = MagicMock(); prod.send_task.side_effect = Exception("down")
        with patch.object(D, "_get_producer", return_value=prod), \
             patch.object(D, "_send_one", side_effect=[Exception("boom"), (True, "ok")]):
            D._dispatch_async("critical", "risk", "t", "b", None, 1)
    assert ("email", "ok") in wb                          # 第二通道不受第一通道异常影响


# ⑧ notify 接线：dispatch 被惰性调用；notif_id 透传
def test_notify_wires_dispatch():
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = [99]
    r = MagicMock(); r.exists.return_value = 0
    with patch.object(N, "_redis", return_value=r), \
         patch("src.data_platform.db.get_conn", return_value=conn), \
         patch("src.alert_notify.dispatch.dispatch") as p_disp:
        N.notify("critical", "system", "t", "b")
    p_disp.assert_called_once()
    assert p_disp.call_args.kwargs.get("notif_id") == 99


# ⑨ report() 回归：info 不进 dispatch
def test_report_info_not_dispatched():
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = [5]
    r = MagicMock(); r.exists.return_value = 0
    with patch.object(N, "_redis", return_value=r), \
         patch("src.data_platform.db.get_conn", return_value=conn), \
         patch("src.alert_notify.dispatch.dispatch") as p_disp, \
         patch.object(N, "_push_channel") as p_pc:
        N.report("盘后报告", "内容")
    p_disp.assert_called_once()
    assert D._LEVEL_RANK.get("info", 0) < 1                # 入口门槛事实
    p_pc.assert_called_once()                              # report 外推走旧路径


# ⑩ 过渡兜底：零 enabled 订阅 → critical+risk 走旧 _push_channel + legacy 回写
def test_legacy_fallback_throttled():
    """B 评 P1：兜底路径保留 15min 节流（E-4 循环告警烧配额回归防线）。"""
    wb = []
    with patch.object(D, "_load_channels", return_value=[]), \
         patch.object(D, "_writeback", lambda n, c, v: wb.append((c, v))), \
         patch.object(D, "_throttled", MagicMock(return_value=True)), \
         patch.object(N, "should_push_external", return_value=True), \
         patch.object(N, "_push_channel") as p_pc:
        D._dispatch_async("critical", "risk", "t", "b", None, 1)
    p_pc.assert_not_called()
    assert ("legacy", "skip:throttled") in wb


def test_claim_guards_double_send():
    """B 评 P2-4：claim rowcount=0（worker 已终态/已认领）→ 弃发。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.rowcount = 0
    with patch("src.data_platform.db.get_conn", return_value=conn):
        assert D._claim(1, "sms") is False
    conn.execute.return_value.rowcount = 1
    with patch("src.data_platform.db.get_conn", return_value=conn):
        assert D._claim(1, "sms") is True
    sql = conn.execute.call_args[0][0]
    assert "'queued'" in sql and "jsonb_build_object(%s, 'sending')" in sql


def test_degrade_respects_claim():
    """降级直发前 claim 失败 → 不发（worker 赢得发送权）。"""
    prod = MagicMock(); prod.send_task.side_effect = Exception("broker timeout-after-accept")
    with patch.object(D, "_load_channels", return_value=[_row()]), \
         patch.object(D, "_writeback", lambda n, c, v: None), \
         patch.object(D, "_throttled", MagicMock(return_value=False)), \
         patch.object(D, "_quota_exceeded", MagicMock(return_value=False)), \
         patch.object(D, "_get_producer", return_value=prod), \
         patch.object(D, "_claim", return_value=False), \
         patch.object(D, "_send_one") as p_send:
        D._dispatch_async("critical", "risk", "t", "b", None, 42)
    p_send.assert_not_called()


def test_legacy_fallback():
    wb = []
    with patch.object(D, "_load_channels", return_value=[]), \
         patch.object(D, "_writeback", lambda n, c, v: wb.append((c, v))), \
         patch.object(D, "_throttled", MagicMock(return_value=False)), \
         patch.object(N, "should_push_external", return_value=True), \
         patch.object(N, "_push_channel", return_value=True) as p_pc:
        D._dispatch_async("critical", "risk", "t", "b", None, 1)
    p_pc.assert_called_once()
    assert ("legacy", "ok") in wb


def test_legacy_fallback_non_critical_writes_empty():
    with patch.object(D, "_load_channels", return_value=[]), \
         patch.object(D, "_throttled", MagicMock(return_value=False)), \
         patch.object(D, "_writeback_empty") as p_empty, \
         patch.object(N, "_push_channel") as p_pc:
        D._dispatch_async("warn", "data", "t", "b", None, 1)   # should_push_external warn=False
    p_pc.assert_not_called(); p_empty.assert_called_once()


# ⑮ 回写契约：单语句合并 + queued 终态守卫 + reason 枚举 + notif_id None 跳过
def test_writeback_contract():
    # notif_id None → 不触 DB
    with patch("src.data_platform.db.get_conn", side_effect=AssertionError("不应触 DB")):
        D._writeback(None, "im", "ok")
    # queued 写带守卫
    conn = MagicMock(); conn.__enter__.return_value = conn
    with patch("src.data_platform.db.get_conn", return_value=conn):
        D._writeback(1, "im", "queued")
    sql = conn.execute.call_args[0][0]
    assert "COALESCE(dispatch,'{}'::jsonb) || jsonb_build_object" in sql
    assert "!~ '^(ok|failed:|skip:)'" in sql
    # 终态写无守卫
    with patch("src.data_platform.db.get_conn", return_value=conn):
        D._writeback(1, "im", "ok")
    sql2 = conn.execute.call_args[0][0]
    assert "!~" not in sql2


def test_writeback_logs_on_failure():
    with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db down")):
        D._writeback(1, "im", "ok")     # 不抛（审计失败必须 log 不反噬）


def test_reason_tokens_constrain():
    """dispatch 值域只允许枚举 token（failed:<token>/skip:<token>）。"""
    ok_reasons = {"throttled", "quota", "disabled", "timeout", "smtp_refused", "smtp_error",
                  "enqueue", "submit", "im_partial", "no_binding", "not_configured", "expired"}
    assert D._REASON_TOKENS == ok_reasons


# ⑯ notifications API 带 dispatch（r[10]）
def test_notifications_api_returns_dispatch():
    from src.web_api.routes.system import notifications_api
    row = [1, "critical", "risk", "t", "b", None, "active", None, None, None,
           {"im": "ok"}]   # psycopg3 自动解析 jsonb→dict，mock 模拟真实返回形态
    conn = MagicMock(); conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [row]
    conn.execute.return_value.fetchone.return_value = [0]
    with patch("src.web_api.routes.system.get_conn", return_value=conn):
        out = notifications_api(status="all", payload={"role": "admin"})
    assert out["items"][0]["dispatch"] == {"im": "ok"}


# ⑰ 通用 system-config 端点封堵 alert_sms_%
def test_generic_systemconfig_blocks_alert_sms():
    from src.web_api.routes.system import update_system_config
    from src.web_api.errors import ApiError
    with patch("src.web_api.routes.system.get_conn") as gc:
        with pytest.raises(ApiError) as ei:
            update_system_config("alert_sms_access_key_secret", {"value": "x"},
                                 payload={"username": "analyst", "role": "analyst"})
    assert ei.value.status_code == 403
    gc.assert_not_called()


# ⑭ task_routes 三队列映射（真实 app 配置断言）
def test_task_routes_three_queues():
    from src.scheduler.app import app as capp
    routes = capp.conf.task_routes
    assert routes["alerts.send_im"]["queue"] == "alerts_im"
    assert routes["alerts.send_email"]["queue"] == "alerts_email"
    assert routes["alerts.send_sms"]["queue"] == "alerts_sms"
    assert "src.scheduler.alert_tasks" in capp.conf.include   # 层3 归位（层2 直 import scheduler 违规）


# 防环闸：alert.push-failed 自身不进外推链
def test_push_failed_loop_breaker():
    with patch.object(D, "_writeback_empty") as p_empty:
        D.dispatch("warn", "system", "告警推送失败[im]: x", "b", code="alert.push-failed", notif_id=3)
    p_empty.assert_called_once()
