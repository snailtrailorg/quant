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
    """档位**多面同键**契约（批 77：实盘面统一挂 `live_control`）。

    档位史（三轮试错，每次都因「同动作多面各判各的」而红）：
    ① 初版把 `task_unfreeze` 并进 `ADMIN_TOOLS`（靠 `resume` 放行），卡片面用 `unfreeze`
       ⇒ 档位错位（拿到工具→出卡→点确认被 denied 的死胡同）——P1 审核改正；
    ② 「统一成 trader 和 admin 都可以解冻」⇒ 退役 `unfreeze`，三面挂 `strategy_control`；
    ③ 用户提出原则「analyst 只能回测与实盘测试，不执行实盘交易」⇒ 推演出 `strategy_control`
       把**研究面**（写策略/因子/回测）与**实盘面**（起 systemd 进程/解冻）混在一键 ⇒ 拆出
       **`live_control`**，实盘面全面迁入（含 `strategy_start/stop`——原 HTTP 挂 strategy_control、
       LLM 挂 trade/halt、IM 挂 trade，**四面对同一动作各判各的**）。

    本闸门是**跨文件契约**：真源是各面**源码文本**里的键字面量——任一面改档即红。
    """

    # 实盘面动作 → 期望键（三面同键的唯一真源）
    LIVE_ACTIONS = {
        "unfreeze_live_task": "live_control",     # web_api/routes/trading.py
        "start_live_task": "live_control",
        "stop_live_task": "live_control",
        "delete_live_task": "live_control",
        "create_live_task": "live_control",
        "start_strategy": "live_control",         # web_api/routes/strategy.py
        "stop_strategy": "live_control",
    }

    def test_http_endpoints_all_live_control(self):
        """HTTP 面：实盘面端点全部 `live_control`（从源码抽 require_perm 字面量）。"""
        import inspect
        from src.web_api.routes import trading as tr, strategy as st
        for fn_name, want in self.LIVE_ACTIONS.items():
            fn = getattr(tr, fn_name, None) or getattr(st, fn_name, None)
            assert fn is not None, f"找不到端点 {fn_name}"
            m = re.search(r'require_perm\(\s*"([^"]+)"\s*\)', inspect.getsource(fn))
            assert m, f"{fn_name} 丢了 require_perm 声明"
            assert m.group(1) == want, f"{fn_name} 挂 {m.group(1)}，应 {want}"

    def test_im_card_face_same_key(self):
        """IM 卡片面：解冻/策略启停的 `_need` 键 = `live_control`（抽字面量，非重复断言常量）。"""
        import inspect
        from src.feishu_bot import ws_client
        src = inspect.getsource(ws_client)
        # _need 段里的元组：抽取 `"<key>" if tool in (...)` / `if tool == "..."`
        seg = re.search(r"_need\s*=.*?\n\s*if _need not in", src, re.S)
        assert seg, "找不到 _need 映射段"
        body = seg.group(0)
        assert '"live_control"' in body, "IM 卡片面未挂 live_control"
        for tool in ("task_unfreeze", "strategy_start", "strategy_stop"):
            assert tool in body, f"IM 卡片面 _need 映射丢了 {tool} 分支"
        # 这三个工具不得再落 trade/halt/strategy_control
        for bad in ('else "trade")', ):
            pass
        # 精确：找 `"live_control" if tool in (` 这一段
        m = re.search(r'"([^"]+)"\s+if\s+tool\s+in\s*\(([^)]*)\)', body)
        assert m, "IM 卡片面未按工具集形式挂档（期望 `\"live_control\" if tool in (...)`）"
        assert m.group(1) == "live_control", f"IM 卡片面实盘工具键={m.group(1)}，应 live_control"
        for tool in ("task_unfreeze", "strategy_start", "strategy_stop"):
            assert f'"{tool}"' in m.group(2), f"{m.group(2)} 缺 {tool}"

    def test_llm_tool_gate_same_key(self, gateway):
        """LLM 档：`live_control` 放行全部实盘面工具；read/trade/halt/resume/strategy_control 不放行。"""
        from src.llm_gateway.gateway import LIVE_TOOLS
        target = {t.name for t in LIVE_TOOLS}
        assert target == {"strategy_start", "strategy_stop", "task_unfreeze"}
        for key in ("read", "trade", "halt", "resume", "strategy_control"):
            names = {t["function"]["name"]
                     for t in gateway._filter_tools("viewer", None, perms={"read", key})}
            assert not (names & target), f"键 {key} 不应放行实盘面工具（应只 live_control）"
        names = {t["function"]["name"]
                 for t in gateway._filter_tools("viewer", None, perms={"read", "live_control"})}
        assert target <= names, f"live_control 应放行 {target}"

    def test_three_faces_agree_on_live_control(self):
        """三面同键总断言：HTTP ≡ IM ≡ LLM 均为 `live_control`（跨文件字面量互比）。"""
        import inspect
        from src.web_api.routes import trading as tr
        from src.feishu_bot import ws_client
        from src.llm_gateway.gateway import LIVE_TOOLS, LLMGateway

        web_key = re.search(r'require_perm\(\s*"([^"]+)"\s*\)',
                            inspect.getsource(tr.unfreeze_live_task)).group(1)
        im_key = re.search(r'"([^"]+)"\s+if\s+tool\s+in\s*\([^)]*task_unfreeze',
                           inspect.getsource(ws_client)).group(1)
        # ⚠️ `import src.llm_gateway.gateway as X` 拿到的是 **LLMGateway 类**，不是模块
        #    （包 __init__ 把 `gateway` 名字重绑成类）⇒ 必须经 getmodule 反查真模块。
        gw_src = inspect.getsource(inspect.getmodule(LLMGateway))
        # LLM 面：LIVE_TOOLS 必须由 live_control 放行（源码级）
        assert 'if "live_control" in perms:' in gw_src and "allowed += LIVE_TOOLS" in gw_src
        assert LIVE_TOOLS
        assert web_key == im_key == "live_control", (
            f"多面档位不一致：web={web_key} im={im_key}，应统一 live_control")

    def test_analyst_excluded_trader_admin_included(self, gateway):
        """**核心验收**：analyst 不得有实盘面能力；trader/admin 必须有（无功能回归）。"""
        from src.data_platform.perms import PERMISSIONS
        from src.llm_gateway.gateway import LIVE_TOOLS
        live = {t.name for t in LIVE_TOOLS}

        # 角色集合层：analyst 无 live_control；trader/admin 有
        assert "live_control" not in PERMISSIONS["analyst"], "analyst 持 live_control = 越界"
        assert "live_control" in PERMISSIONS["trader"]
        assert "live_control" in PERMISSIONS["admin"]

        # 工具档层：analyst 看不到任何实盘面工具；trader/admin 全看到
        def names(role):
            return {t["function"]["name"] for t in gateway._filter_tools(role, None)}
        assert not (names("analyst") & live), "analyst 不应看到实盘面工具"
        assert live <= names("trader"), "trader 应看到全部实盘面工具（拆分不得削权）"
        assert live <= names("admin"), "admin 应看到全部实盘面工具"

    def test_no_strategy_control_on_live_endpoints(self):
        """反向钉：实盘面端点**不得**再回落 `strategy_control`（拆分的意义就在此）。"""
        import inspect
        from src.web_api.routes import trading as tr, strategy as st
        for fn_name in self.LIVE_ACTIONS:
            fn = getattr(tr, fn_name, None) or getattr(st, fn_name, None)
            src = inspect.getsource(fn)
            assert "require_perm(\"strategy_control\")" not in src, \
                f"{fn_name} 又挂回 strategy_control（实盘面泄漏给 analyst）"

    def test_live_control_key_registered_with_frontend(self):
        """新键五处连带：注册表 / permGroups / locales 双语。"""
        from src.data_platform.perm_registry import API_PERM_KEYS
        assert "live_control" in API_PERM_KEYS
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        pg = (root / "web" / "src" / "permGroups.js").read_text(encoding="utf-8")
        assert "'live_control'" in pg
        loc = (root / "web" / "src" / "locales" / "index.js").read_text(encoding="utf-8")
        assert loc.count("key_live_control:") == 2, "key_live_control 需中英各一条"

    def test_operational_tools_covers_live_tools_for_card_flow(self):
        """`OPERATIONAL_TOOLS` 须含实盘面工具——否则 handlers 会把它当读工具直执行。"""
        from src.llm_gateway.gateway import LIVE_TOOLS, OPERATIONAL_TOOLS
        names = {t.name for t in OPERATIONAL_TOOLS}
        assert {t.name for t in LIVE_TOOLS} <= names
