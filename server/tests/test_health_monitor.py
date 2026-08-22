"""15-服务监控 测试：判定规则（纯函数）+ Prometheus 渲染 + 暴露端。
2026-08-18 盲审 C/D 修订后语义：evaluate 返回 (findings, state)；R1 跳过 auto-restart；
R4 需连续 2 轮；R6 交易时段 tick 停滞；HELP 按族唯一。"""
import pytest


# ——— 判定规则（evaluate，纯函数）———

def _snap(**over):
    snap = {"ts": 0.0,
            "units": {"quant-md-hub@quant": {"ActiveState": "active", "SubState": "running", "NRestarts": "0"}},
            "deps": {"postgres": True, "valkey": True},
            "hub": {"gen": 10, "subs": 1, "ticks": 100, "sess_ticks": 100, "bars": 9,
                    "dropped_pg": 0, "tick_age": 3.0},
            "tasks": {}}
    snap.update(over)
    return snap


class TestEvaluate:
    def test_all_green_no_events(self):
        from src.health_monitor.monitor import evaluate
        findings, state = evaluate(_snap(tasks={"7": {"md": "hub", "bars": 78, "lag": 41.0, "frozen": 0}}))
        assert findings == []
        assert state["hub_lost_streak"] == 0

    def test_unit_down_critical(self):
        from src.health_monitor.monitor import evaluate
        snap = _snap(units={"quant-md-hub@quant": {"ActiveState": "failed", "SubState": "failed", "NRestarts": "3"}},
                     hub=None)   # unit 挂了心跳自然也没了
        f1, s1 = evaluate(snap)
        assert {f["rule_id"] for f in f1} == {"unit_down"}   # hub 心跳缺失首轮只累计（D-F2 连续 2 轮）
        f2, _ = evaluate(snap, s1)
        assert {f["rule_id"] for f in f2} == {"unit_down", "hub_hb_lost"}
        assert all(f["severity"] == "critical" for f in f2)

    def test_unit_auto_restart_skipped(self):
        """D-F2：RestartSec 窗口的 auto-restart 是设计内自愈，不告 unit_down（R2 计数沿会报）。"""
        from src.health_monitor.monitor import evaluate
        findings, _ = evaluate(_snap(units={"quant-md-hub@quant":
                                            {"ActiveState": "activating", "SubState": "auto-restart", "NRestarts": "1"}}))
        assert not any(f["rule_id"] == "unit_down" for f in findings)

    def test_dep_down_critical_each(self):
        from src.health_monitor.monitor import evaluate
        findings, _ = evaluate(_snap(deps={"postgres": False, "valkey": True}))
        assert [f["component"] for f in findings] == ["postgres"]

    def test_hub_hb_lost_needs_two_rounds(self):
        """D-F2：hub 设计内重启首跳心跳 60s+——单轮缺失不告，连续 2 轮才 critical。"""
        from src.health_monitor.monitor import evaluate
        f1, s1 = evaluate(_snap(hub=None))
        assert not any(f["rule_id"] == "hub_hb_lost" for f in f1)
        assert s1["hub_lost_streak"] == 1
        f2, s2 = evaluate(_snap(hub=None), s1)
        assert any(f["rule_id"] == "hub_hb_lost" for f in f2)
        # 恢复即清零
        f3, s3 = evaluate(_snap(), s2)
        assert not any(f["rule_id"] == "hub_hb_lost" for f in f3) and s3["hub_lost_streak"] == 0

    def test_hub_hb_lost_not_fired_when_valkey_down(self):
        """Valkey 也挂时只报 dep_down，不误报 hub（区分不出 key 过期和存储不可达）。"""
        from src.health_monitor.monitor import evaluate
        findings, _ = evaluate(_snap(hub=None, deps={"postgres": True, "valkey": False}))
        assert not any(f["rule_id"] == "hub_hb_lost" for f in findings)
        assert any(f["rule_id"] == "dep_down" and f["component"] == "valkey" for f in findings)

    def test_r6_tick_stalled_in_session(self, monkeypatch):
        """C-S1：删自杀后，时段内 sess_ticks 零增长 ≥2 轮 → critical（半开连接/线程挂死抓手）。
        阈值语义：第 1 轮建基线（stall=0），其后每轮零增长 stall+1，stall≥2 触发（约 60-90s 无 tick）。"""
        from src.health_monitor import monitor
        monkeypatch.setattr(monitor, "_in_session", lambda: True)
        hub = {"gen": 10, "subs": 1, "ticks": 500, "sess_ticks": 42, "bars": 9,
               "dropped_pg": 0, "tick_age": 400.0}
        f1, s1 = monitor.evaluate(_snap(hub=hub))
        assert not any(f["rule_id"] == "hub_tick_stalled" for f in f1)
        f2, s2 = monitor.evaluate(_snap(hub=hub), s1)
        assert not any(f["rule_id"] == "hub_tick_stalled" for f in f2)   # stall=1 未达阈
        f3, s3 = monitor.evaluate(_snap(hub=dict(hub, tick_age=520.0)), s2)
        fired = [f for f in f3 if f["rule_id"] == "hub_tick_stalled"]
        assert fired and fired[0]["severity"] == "critical"

    def test_task_frozen_warning(self):
        from src.health_monitor.monitor import evaluate
        findings, _ = evaluate(_snap(tasks={"7": {"md": "hub", "bars": 78, "lag": 900.0, "frozen": 1}}))
        assert findings and findings[0]["rule_id"] == "task_blind" and findings[0]["severity"] == "warning"

    def test_empty_units_no_unit_down(self):
        """D-F5：systemctl 采集失败（units 空）= 证据缺失，不判 unit 全挂。"""
        from src.health_monitor.monitor import evaluate
        findings, _ = evaluate(_snap(units={}))
        assert not any(f["rule_id"] == "unit_down" for f in findings)


# ——— run_check 通知链独立性（D-F1 回归：Valkey 挂时告警必须仍能发出）———

class TestRunCheckChain:
    def test_notify_survives_valkey_outage(self, monkeypatch):
        """Valkey 完全不可达：dep_down(valkey) critical 必须到达 _notify（不能被 try 吞掉）。"""
        from unittest.mock import MagicMock
        from src.health_monitor import monitor, collector

        def _boom():
            raise ConnectionError("valkey down")
        monkeypatch.setattr(collector, "_valkey", _boom)   # monitor.run_check 从 collector 导入同一符号
        monkeypatch.setattr(collector, "systemctl_units", lambda units: {})
        # PG 也探不到 → dep_down 两条；collect 的 PG 段也要 mock 掉
        import src.data_platform.db as db
        monkeypatch.setattr(db, "get_conn", _boom)

        notified = []
        monkeypatch.setattr(monitor, "_notify", lambda sev, title, body: notified.append((sev, title)))
        monkeypatch.setattr(monitor, "_write_event", lambda *a, **k: None)
        monitor.run_check()
        assert any("valkey" in t for _, t in notified), "Valkey 宕机的 critical 必须仍被通知"

    def test_restart_events_bypass_state_machine(self, monkeypatch):
        """D-F4：unit_restarted 是计数沿，不进电平状态机（否则 30s 后必跟假恢复）。"""
        from src.health_monitor import monitor

        class FakeR:
            def __init__(self):
                self.d = {}
            def get(self, k):
                return self.d.get(k)
            def set(self, k, v, ex=None):
                self.d[k] = v
        r = FakeR()
        r.d["quant:hm:nr:quant-md-hub@quant"] = "0"
        snap = _snap(units={"quant-md-hub@quant": {"ActiveState": "active", "SubState": "running", "NRestarts": "1"}})
        evts = monitor._detect_restarts(snap, r)
        assert len(evts) == 1 and evts[0]["rule_id"] == "unit_restarted"


# ——— Prometheus 渲染 ———

class TestRenderPrometheus:
    def _snap(self):
        return _snap(units={"quant-md-hub@quant": {"ActiveState": "active", "SubState": "running", "NRestarts": "2"},
                            "quant-web-api@quant": {"ActiveState": "active", "SubState": "running", "NRestarts": "0"}},
                     hub={"gen": 10, "subs": 1, "ticks": 193, "sess_ticks": 100, "bars": 9,
                          "dropped_pg": 0, "tick_age": 3.2},
                     tasks={"7": {"md": "hub", "bars": 78, "lag": 41.6, "frozen": 0}})

    def test_help_unique_per_family(self):
        """D-F3：多 unit 场景 HELP/TYPE 每族恰好一组且在样本之前——严格解析器不拒收。"""
        from src.health_monitor.collector import render_prometheus
        text = render_prometheus(self._snap())
        lines = text.splitlines()
        helps = [l for l in lines if l.startswith("# HELP")]
        assert len(helps) == len(set(helps)), "HELP 行不得重复"
        for i, l in enumerate(lines):
            if l.startswith("#"):
                continue
            fam = l.split("{")[0].split(" ")[0]
            first_help_idx = next(i2 for i2, l2 in enumerate(lines)
                                  if l2.startswith(f"# HELP {fam} "))
            assert first_help_idx < i, f"样本 {l} 必须在其族的 HELP 之后"
        assert 'quant_unit_up{unit="quant-web-api@quant"} 1' in text

    def test_format_and_metrics_present(self):
        from src.health_monitor.collector import render_prometheus
        text = render_prometheus(self._snap())
        assert 'quant_unit_nrestarts{unit="quant-md-hub@quant"} 2' in text
        assert 'quant_dep_up{dep="postgres"} 1' in text
        assert 'quant_hub_gen 10' in text
        assert '# TYPE quant_hub_ticks_total counter' in text   # 计数语义（D 陷阱 8）
        assert 'quant_task_frozen{task="7"} 0' in text
        assert 'quant_task_lag_seconds{task="7"} 41.6' in text

    def test_hub_absent_renders_present_zero(self):
        from src.health_monitor.collector import render_prometheus
        text = render_prometheus(_snap(hub=None))
        assert "quant_hub_hb_present 0" in text


# ——— 暴露端 ———

class TestEndpoints:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        return TestClient(app)

    @pytest.fixture
    def admin_client(self):
        """鉴权端点测试：patch verify_jwt 伪造 admin payload（require_role 是每次新建的闭包，
        dependency_overrides 键对不上，走真实链路+假 JWT 更贴近生产）。"""
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        from src.web_api import auth as _auth
        from unittest.mock import patch as _patch
        with _patch.object(_auth, "verify_jwt",
                           return_value={"sub": "1", "username": "admin", "role": "admin"}):
            client = TestClient(app)
            client.headers.update({"Authorization": "Bearer test-token"})
            yield client

    def test_healthz_ok_no_deps(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200 and r.json()["status"] == "ok"

    def test_health_alias_ok(self, client):
        assert client.get("/health").status_code == 200

    def test_metrics_prometheus_text(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "# HELP quant_unit_up" in r.text

    def test_components_endpoint_returns_collector_shape(self, admin_client, monkeypatch):
        """SM2：组件矩阵端点 = collector 快照原样（与 /metrics 同源同口径）。"""
        from src.health_monitor import collector
        fake = _snap(ts=123.0)
        monkeypatch.setattr(collector, "collect", lambda: fake)
        r = admin_client.get("/api/health/components")
        assert r.status_code == 200
        body = r.json()
        assert "units" in body and "deps" in body and "hub" in body
        assert body["hub"]["gen"] == 10

    def test_events_endpoint_queries_health_event(self, admin_client):
        """SM2：事件流端点读 health_event 倒序 + limit 传参。"""
        from unittest.mock import MagicMock
        from unittest.mock import patch
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = [
            ("2026-08-18 14:00:00+08", "unit_down", "quant-md-hub@quant", "critical", "ActiveState=failed")]
        from src.web_api.routes import system as system_route
        with patch.object(system_route, "get_conn", return_value=mock_conn):
            r = admin_client.get("/api/health/events", params={"limit": 50})
        assert r.status_code == 200
        evts = r.json()["events"]
        assert evts and evts[0]["rule"] == "unit_down" and evts[0]["severity"] == "critical"
        sql = mock_conn.execute.call_args.args[0]
        assert "health_event" in sql and "DESC" in sql
        assert mock_conn.execute.call_args.args[1] == (50,)
