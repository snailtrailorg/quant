"""15-服务监控 测试：判定规则（纯函数）+ Prometheus 渲染 + 暴露端（S6 修订 2026-08-18 配套）。"""
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
        snap = _snap(units={"quant-md-hub@quant": {"ActiveState": "active", "SubState": "running", "NRestarts": "0"}},
                     hub={"gen": 10, "subs": 1, "ticks": 100, "sess_ticks": 100, "bars": 9,
                          "dropped_pg": 0, "tick_age": 3.0},
                     tasks={"7": {"md": "hub", "bars": 78, "lag": 41.0, "frozen": 0}})
        assert evaluate(snap) == []

    def test_unit_down_critical(self):
        from src.health_monitor.monitor import evaluate
        snap = _snap(units={"quant-md-hub@quant": {"ActiveState": "failed", "SubState": "failed", "NRestarts": "3"}},
                     hub=None)   # unit 挂了心跳自然也没了——但 Valkey 活，hub_hb_lost 一并触发（两个都对）
        evts = evaluate(snap)
        assert {e["rule_id"] for e in evts} == {"unit_down", "hub_hb_lost"}
        assert all(e["severity"] == "critical" for e in evts)

    def test_dep_down_critical_each(self):
        from src.health_monitor.monitor import evaluate
        evts = evaluate(_snap(deps={"postgres": False, "valkey": True}))
        assert [e["component"] for e in evts] == ["postgres"]

    def test_hub_hb_lost_only_when_valkey_reachable(self):
        """hub=None 且 Valkey 活 → hub 心跳丢失；Valkey 也挂 → 只报 dep_down（不误报 hub）。"""
        from src.health_monitor.monitor import evaluate
        evts = evaluate(_snap(hub=None))
        assert any(e["rule_id"] == "hub_hb_lost" for e in evts)
        evts2 = evaluate(_snap(hub=None, deps={"postgres": True, "valkey": False}))
        assert not any(e["rule_id"] == "hub_hb_lost" for e in evts2)

    def test_task_frozen_warning(self):
        from src.health_monitor.monitor import evaluate
        snap = _snap(tasks={"7": {"md": "hub", "bars": 78, "lag": 900.0, "frozen": 1}})
        evts = evaluate(snap)
        assert evts and evts[0]["rule_id"] == "task_blind" and evts[0]["severity"] == "warning"


# ——— Prometheus 渲染 ———

class TestRenderPrometheus:
    def _snap(self):
        return _snap(units={"quant-md-hub@quant": {"ActiveState": "active", "SubState": "running", "NRestarts": "2"}},
                     hub={"gen": 10, "subs": 1, "ticks": 193, "sess_ticks": 100, "bars": 9,
                          "dropped_pg": 0, "tick_age": 3.2},
                     tasks={"7": {"md": "hub", "bars": 78, "lag": 41.6, "frozen": 0}})

    def test_format_and_metrics_present(self):
        from src.health_monitor.collector import render_prometheus
        text = render_prometheus(self._snap())
        assert "# HELP quant_unit_up" in text and "# TYPE quant_unit_up gauge" in text
        assert 'quant_unit_up{unit="quant-md-hub@quant"} 1' in text
        assert 'quant_unit_nrestarts{unit="quant-md-hub@quant"} 2' in text
        assert 'quant_dep_up{dep="postgres"} 1' in text
        assert 'quant_hub_gen 10' in text
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
