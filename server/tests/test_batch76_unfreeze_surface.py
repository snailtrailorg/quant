"""批 76 · F2 解冻面验收钉：Web 端点（人工解冻/确认链预览/事实查询）+ IM 工具（Web 确认链）。

桩模式：直调路由函数（DB/审计/握手模块打桩）+ HTTP 层身份闸（verify_jwt 打桩）双档；
IM 侧打桩 FeishuClient 与 freeze_event 握手，断言的是**回执内容与调用因果**：
- IM `task_unfreeze` **不得直接解冻**（明文/直改路径禁行）——必须发一次性 token 的 Web 深链；
- Web 端点两形态（无 token=manual_web；带 token=manual_im 且 token 一次性、须与 tid 一致）；
- 未配置 web_base_url 时 IM 诚实降级（不静默失败）。
"""
import re
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
    """解冻档位**三面同键**契约（2026-10-01 二次裁定：统一挂 `strategy_control`）。

    历史两轮：
    ① 原实现把 `task_unfreeze` 并进 `ADMIN_TOOLS`（靠 `resume` 放行），而卡片面用 `unfreeze`
       键 ⇒ 档位错位（拿到工具→出卡→点确认被 denied 的死胡同）——P1 审核改正为独立
       `UNFREEZE_TOOLS` + `unfreeze` 键；
    ② 随后用户裁定「统一成 trader 和 admin 都可以解冻」⇒ **退役 `unfreeze` 键**，三面统一挂
       既有 `strategy_control`（Web 端点 / IM 卡片面 / LLM 工具档），零注册表面。

    本闸门是**跨文件契约**：真源是 `feishu_bot/ws_client.py` 的 `_need` 映射**源码文本**、
    以及 `web_api/routes/trading.py` 端点的 `require_perm(...)` 字面量——任一面改档即红。
    """

    def test_web_endpoint_and_llm_tool_gate_same_key(self):
        """Web 端点的 require_perm 键 ≡ LLM 侧放行 UNFREEZE_TOOLS 的键（跨文件抽字面量互比）。"""
        import inspect
        from src.web_api.routes import trading as tr
        from src.llm_gateway.gateway import UNFREEZE_TOOLS

        # Web 面：从端点源码抽 require_perm("<键>")
        src = inspect.getsource(tr.unfreeze_live_task)
        m = re.search(r'require_perm\(\s*"([^"]+)"\s*\)', src)
        assert m, "unfreeze_live_task 端点丢了 require_perm 声明"
        web_key = m.group(1)

        # IM 卡片面：从 _need 映射抽 task_unfreeze 的键
        from src.feishu_bot import ws_client
        wsrc = inspect.getsource(ws_client)
        m2 = re.search(r'else\s+"([^"]+)"\s+if\s+tool\s*==\s*"task_unfreeze"', wsrc)
        assert m2, "ws_client 的 _need 映射丢了 task_unfreeze 分支（IM 卡片面契约变更）"
        im_key = m2.group(1)

        # LLM 面：该键必须能放行 UNFREEZE_TOOLS，其余键不能
        gw = __import__("src.llm_gateway.gateway", fromlist=["LLMGateway"]).LLMGateway()
        target = {t.name for t in UNFREEZE_TOOLS}
        assert "strategy_control" not in target
        for key in ("read", "trade", "halt", "resume"):
            names = {t["function"]["name"]
                     for t in gw._filter_tools("viewer", None, perms={"read", key})}
            assert not (names & target), f"键 {key} 不应放行解冻工具"

        assert web_key == im_key == "strategy_control", (
            f"三面档位不一致：web={web_key} im={im_key}，应统一为 strategy_control")
        names = {t["function"]["name"]
                 for t in gw._filter_tools("viewer", None, perms={"read", web_key})}
        assert target <= names, f"键 {web_key} 应放行 {target}（与 Web/IM 面同键）"

    def test_trader_and_admin_can_both_unfreeze(self, gateway):
        """裁定落地：trader 与 admin 都可解冻（strategy_control 档）；viewer/analyst 不可。

        `analyst` 也持 strategy_control（研究档）⇒ 同样可见——这是与启停任务一致的既有语义，
        非本批引入；Web 端点同键，行为一致（无「两面不同判」）。
        """
        def names(role):
            return {t["function"]["name"] for t in gateway._filter_tools(role, None)}
        for role in ("trader", "admin"):
            assert "task_unfreeze" in names(role), f"{role} 应可解冻（strategy_control 档）"
        assert "task_unfreeze" not in names("viewer")

        # perms 动态组路径（IM 实走这条）
        def by_perms(p):
            return {t["function"]["name"]
                    for t in gateway._filter_tools("viewer", None, perms=p)}
        assert "task_unfreeze" in by_perms({"read", "strategy_control"})
        assert "task_unfreeze" not in by_perms({"read", "trade", "halt"})

    def test_unfreeze_perm_key_retired(self):
        """`unfreeze` 已从注册表退役（统一挂 strategy_control，零新注册表面）。"""
        from src.data_platform.perm_registry import API_PERM_KEYS
        assert "unfreeze" not in API_PERM_KEYS
        assert "strategy_control" in API_PERM_KEYS
        import re as _re
        from pathlib import Path
        # 前端：permGroups 与 locales 不得再残留 unfreeze 权限键词条
        pg = Path(__file__).resolve().parents[2] / "web" / "src" / "permGroups.js"
        assert "'unfreeze'" not in pg.read_text(encoding="utf-8")
        loc = (Path(__file__).resolve().parents[2] / "web" / "src" / "locales" / "index.js").read_text(encoding="utf-8")
        assert "key_unfreeze:" not in loc
        # IM 卡片面：_need 映射里不得再出现 "unfreeze" 键字面量
        from src.feishu_bot import ws_client
        import inspect
        _need_src = _re.search(r"_need\s*=.*?\n\s*if _need not in", inspect.getsource(ws_client), _re.S)
        assert _need_src, "找不到 _need 映射段"
        assert '"unfreeze"' not in _need_src.group(0)

    def test_operational_tools_covers_unfreeze_for_card_flow(self):
        """`OPERATIONAL_TOOLS` 须含 task_unfreeze——否则 handlers 会把它当读工具直执行。"""
        from src.llm_gateway.gateway import OPERATIONAL_TOOLS
        names = {t.name for t in OPERATIONAL_TOOLS}
        assert "task_unfreeze" in names
