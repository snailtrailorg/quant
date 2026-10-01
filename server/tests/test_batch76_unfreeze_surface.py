"""批 76 · F2 解冻面验收钉：Web 端点（人工解冻/确认链预览/事实查询）+ IM 工具（Web 确认链）。

桩模式：直调路由函数（DB/审计/握手模块打桩）+ HTTP 层身份闸（verify_jwt 打桩）双档；
IM 侧打桩 FeishuClient 与 freeze_event 握手，断言的是**回执内容与调用因果**：
- IM `task_unfreeze` **不得直接解冻**（明文/直改路径禁行）——必须发一次性 token 的 Web 深链；
- Web 端点两形态（无 token=manual_web；带 token=manual_im 且 token 一次性、须与 tid 一致）；
- 未配置 web_base_url 时 IM 诚实降级（不静默失败）。
"""
from unittest.mock import MagicMock, patch

import pytest

ADMIN = {"sub": "1", "username": "admin", "role": "admin", "db_role": "admin"}
TRADER = {"sub": "2", "username": "trader", "role": "trader", "db_role": "trader"}
VIEWER = {"sub": "3", "username": "viewer", "role": "viewer", "db_role": "viewer"}


def _conn(row=("running", "600000.SHSE")):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value = MagicMock(fetchone=MagicMock(return_value=row))
    return conn


def _call_endpoint(mod_name, fn_name, *args, **kw):
    from src.web_api.routes import trading as tr
    return getattr(tr, fn_name)(*args, **kw)


class TestUnfreezeWebEndpoint:
    def test_web_direct_path_no_token(self):
        """无 token（Web 直接点）→ channel=manual_web，操作者=登录用户，审计留痕。"""
        with patch("src.web_api.routes.trading.get_conn", return_value=_conn()), \
             patch("src.data_platform.freeze_event.request_unfreeze",
                   return_value={"operator": "admin"}) as req, \
             patch("src.web_api.routes.trading.audit_log") as au:
            out = _call_endpoint("trading", "unfreeze_live_task", 5, body=None, payload=ADMIN)
        assert out["ok"] and out["channel"] == "manual_web" and out["symbol"] == "600000.SHSE"
        assert req.call_args.kwargs["channel"] == "manual_web"
        assert req.call_args.args[0] == 5
        assert au.call_args.args[1] == "unfreeze_live_task"

    def test_im_confirm_chain_token_path(self):
        """带 confirm_token（IM 确认链）→ 一次性消费 + channel=manual_im（记 IM 发起人）。"""
        with patch("src.web_api.routes.trading.get_conn", return_value=_conn()), \
             patch("src.data_platform.freeze_event.consume_confirm_token",
                   return_value={"tid": 5, "operator": "william"}), \
             patch("src.data_platform.freeze_event.request_unfreeze", return_value={}) as req, \
             patch("src.web_api.routes.trading.audit_log"):
            out = _call_endpoint("trading", "unfreeze_live_task", 5,
                                 body={"confirm_token": "tok"}, payload=ADMIN)
        assert out["channel"] == "manual_im"
        assert "william" in req.call_args.kwargs["operator"]

    def test_token_tid_mismatch_rejected(self):
        """确认链接与目标任务不一致 ⇒ 400 且 token 已作废（防 A 任务链接解冻 B 任务）。"""
        from src.web_api.errors import ApiError
        with patch("src.web_api.routes.trading.get_conn", return_value=_conn()), \
             patch("src.data_platform.freeze_event.consume_confirm_token",
                   return_value={"tid": 9, "operator": "w"}):
            with pytest.raises(ApiError) as e:
                _call_endpoint("trading", "unfreeze_live_task", 5,
                               body={"confirm_token": "tok"}, payload=ADMIN)
        assert e.value.status_code == 400 and e.value.code == "UNFREEZE_TOKEN_MISMATCH"

    def test_token_invalid_or_replayed(self):
        """已被用过/过期（GETDEL 返回 None）⇒ 400 明确提示回 IM 重发。"""
        from src.web_api.errors import ApiError
        with patch("src.web_api.routes.trading.get_conn", return_value=_conn()), \
             patch("src.data_platform.freeze_event.consume_confirm_token", return_value=None):
            with pytest.raises(ApiError) as e:
                _call_endpoint("trading", "unfreeze_live_task", 5,
                               body={"confirm_token": "used"}, payload=ADMIN)
        assert e.value.code == "UNFREEZE_TOKEN_INVALID"

    def test_task_not_found(self):
        from src.web_api.errors import ApiError
        with patch("src.web_api.routes.trading.get_conn", return_value=_conn(row=None)):
            with pytest.raises(ApiError) as e:
                _call_endpoint("trading", "unfreeze_live_task", 5, body=None, payload=ADMIN)
        assert e.value.status_code == 404

    def test_channel_down_is_honest_503(self):
        """Valkey 不可达 ⇒ 503（不许假装成功——解冻命令必须真的到得了 worker）。"""
        from src.web_api.errors import ApiError
        with patch("src.web_api.routes.trading.get_conn", return_value=_conn()), \
             patch("src.data_platform.freeze_event.request_unfreeze",
                   side_effect=RuntimeError("valkey down")):
            with pytest.raises(ApiError) as e:
                _call_endpoint("trading", "unfreeze_live_task", 5, body=None, payload=ADMIN)
        assert e.value.status_code == 503 and e.value.code == "UNFREEZE_CHANNEL_UNAVAILABLE"

    def test_preview_endpoint_peek_only(self):
        """预览端点只读不消费（页面刷新多次不应把 token 吃掉）。"""
        with patch("src.data_platform.freeze_event.peek_confirm_token",
                   return_value={"tid": 5, "symbol": "600000.SHSE", "freeze_type": "ts_gap",
                                 "operator": "william", "created_at": 1}) as peek:
            out = _call_endpoint("trading", "get_unfreeze_request", token="tok", payload=ADMIN)
        assert out["tid"] == 5 and out["freeze_type"] == "ts_gap"
        assert peek.call_args.args[0] == "tok"

    def test_preview_invalid_token(self):
        from src.web_api.errors import ApiError
        with patch("src.data_platform.freeze_event.peek_confirm_token", return_value=None):
            with pytest.raises(ApiError) as e:
                _call_endpoint("trading", "get_unfreeze_request", token="x", payload=ADMIN)
        assert e.value.status_code == 400

    def test_freeze_events_endpoint(self):
        with patch("src.data_platform.freeze_event.list_events",
                   return_value=[{"id": 1, "freeze_type": "seq_gap"}]) as le:
            out = _call_endpoint("trading", "list_freeze_events", 5, limit=10, payload=ADMIN)
        assert out["events"][0]["freeze_type"] == "seq_gap"
        assert le.call_args.kwargs == {"task_id": 5, "limit": 10}


class TestUnfreezeAuthGate:
    """HTTP 层：Web 端点权限=`strategy_control`（2026-10-01 裁定：与启停任务同页同键，零注册表面）。

    故 trader **允许**（与启停任务对称）；无 strategy_control 的 viewer/analyst ⇒ 403。
    IM 侧档位不同（`unfreeze` 键，仅 Admin）——两入口的权限梯度差异见任务文件 §交付记录。
    """

    def _post(self, who):
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        with patch("src.web_api.auth.verify_jwt", return_value=who), \
             patch("src.web_api.routes.trading.get_conn", return_value=_conn()), \
             patch("src.data_platform.db.get_conn", return_value=_conn()), \
             patch("src.data_platform.freeze_event.request_unfreeze", return_value={}):
            return TestClient(app).post("/api/live-task/5/unfreeze", json={},
                                        headers={"Authorization": "Bearer t"})

    def test_admin_allowed(self):
        assert self._post(ADMIN).status_code == 200

    def test_trader_allowed_same_key_as_start_stop(self):
        assert self._post(TRADER).status_code == 200

    def test_viewer_forbidden(self):
        assert self._post(VIEWER).status_code == 403


class TestImTaskUnfreezeTool:
    """IM 侧：`task_unfreeze` 只发 Web 确认链深链，**不在 IM 内直接解冻**。"""

    def _run(self, tool_args="5", base="https://q.example.com"):
        fc = MagicMock()
        with patch("src.im_bot.feishu_client.get_feishu_client", return_value=fc), \
             patch("src.data_platform.freeze_event.web_base_url", return_value=base), \
             patch("src.data_platform.freeze_event.make_confirm_token",
                   return_value="TOKEN123") as mk, \
             patch("src.data_platform.freeze_event.list_events", return_value=[]), \
             patch("src.data_platform.db.get_conn",
                   return_value=MagicMock(**{"__enter__.return_value.execute.return_value.fetchone.return_value": ("600000.SHSE",)})), \
             patch("src.data_platform.audit.audit_log"), \
             patch("src.data_platform.freeze_event.request_unfreeze") as req:
            from src.im_bot.feishu_client import execute_confirmed_tool
            ok = execute_confirmed_tool("ou_x", "task_unfreeze", tool_args, username="william")
        return ok, fc, mk, req

    def test_replies_web_deeplink_and_does_not_unfreeze(self):
        ok, fc, mk, req = self._run()
        assert ok is True
        text = fc.send_text.call_args.args[1]
        assert "/unfreeze-confirm?token=TOKEN123" in text and "https://q.example.com" in text
        assert mk.call_args.kwargs["operator"] == "william"
        assert not req.called, "IM 侧直改解冻（未确认就写请求键）=越权路径"

    def test_degrades_when_web_base_missing(self):
        """未配置 web_base_url：诚实提示到 Web 手工解冻（不静默、也不发死链）。"""
        ok, fc, mk, req = self._run(base="")
        assert ok is True
        text = fc.send_text.call_args.args[1]
        assert "web_base_url" in text and "手工解冻" in text
        assert not mk.called


class TestToolGatingContract:
    """P1（2026-10-01 审核）：LLM 聊天工具档位必须与 IM 卡片确认面**同键**。

    回归场景（原缺陷）：`task_unfreeze` 曾被并进 `ADMIN_TOOLS`、靠 `resume` 键放行，
    而卡片面用 `unfreeze` 键 ⇒ ① 有 resume 无 unfreeze 的人看得到工具、点确认被 denied
    （死胡同）；② 有 unfreeze 无 resume 的人看不到工具（勾了键不生效）。
    修法=独立 `UNFREEZE_TOOLS` 靠 `unfreeze` 键放行。

    本闸门是**跨文件契约**：真源是 `feishu_bot/ws_client.py` 的 `_need` 映射源码文本——
    两处任一方改了档位归属，此测试即红（不靠人记）。
    """

    def test_llm_tool_gate_is_unfreeze_key_not_resume(self, gateway):
        """有 unfreeze 无 resume ⇒ 看得到；有 resume 无 unfreeze ⇒ 看不到。"""
        with_unf = {t["function"]["name"]
                    for t in gateway._filter_tools("viewer", None, perms={"read", "unfreeze"})}
        assert "task_unfreeze" in with_unf
        with_res = {t["function"]["name"]
                    for t in gateway._filter_tools("viewer", None, perms={"read", "resume"})}
        assert "task_unfreeze" not in with_res, "task_unfreeze 不得靠 resume 键放行（P1 回归）"

    def test_im_tool_gate_and_llm_tool_gate_same_key(self):
        """两侧档位映射同源：从 ws_client 源码抽出 task_unfreeze 的键，与 LLM 侧常量比对。"""
        import inspect
        import re
        from src.feishu_bot import ws_client
        from src.llm_gateway import gateway as gw

        src = inspect.getsource(ws_client)
        m = re.search(r'else\s+"(\w+)"\s+if\s+tool\s*==\s*"task_unfreeze"', src)
        assert m, "ws_client 的 _need 档位映射丢了 task_unfreeze 分支（IM 卡片面契约变更）"
        im_key = m.group(1)
        assert im_key == "unfreeze", f"IM 卡片面档位={im_key}，应为 unfreeze"

        # LLM 侧：该键必须能拿到工具，其余键都必须拿不到
        for key in ("read", "trade", "halt", "resume", "strategy_control"):
            names = {t["function"]["name"]
                     for t in gw._filter_tools("viewer", None, perms={"read", key})}
            assert "task_unfreeze" not in names, f"键 {key} 不应放行 task_unfreeze"
        names = {t["function"]["name"]
                 for t in gw._filter_tools("viewer", None, perms={"read", im_key})}
        assert "task_unfreeze" in names, f"键 {im_key} 应放行 task_unfreeze（与 IM 面同源）"

    def test_operational_tools_covers_unfreeze_for_card_flow(self):
        """`OPERATIONAL_TOOLS` 须含 task_unfreeze——否则 handlers 会把它当读工具直执行。"""
        from src.llm_gateway.gateway import OPERATIONAL_TOOLS
        names = {t.name for t in OPERATIONAL_TOOLS}
        assert "task_unfreeze" in names

    def test_role_default_still_admin_only(self, gateway):
        """role 档（perms=None，Web 兼容路径）不变：admin 可见，trader/viewer 不可见。"""
        def names(role):
            return {t["function"]["name"] for t in gateway._filter_tools(role, None)}
        assert "task_unfreeze" in names("admin")
        assert "task_unfreeze" not in names("trader")
        assert "task_unfreeze" not in names("viewer")
