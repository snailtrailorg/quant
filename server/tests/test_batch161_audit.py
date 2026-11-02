"""批 161 · dep_audit 异步幂等心跳化——替代 155 契约钉（新语义）+161 新增六套。

语义变更（方案 v2）：契约瘦身（last_complete/os/映射+兼容 :rows 键）；表=rows 真源
（judge 随行落列）；心跳三态（done/zombie>10min/interrupted）；单线程（ThreadPool 删）；
refresh 参数删（缓存恒读+7d TTL 轮转）；时限=名义 172800。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


def test_local_pkg_zero_upstream():
    """local 包不发起 pypi 请求（155 原语义保留——批161 源码断言更新）。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert 'if local:' in src and 'managed = "local"' in src
    assert "pypi_versions_cached" not in src.split('if local:')[1].split("elif")[0]


def test_no_threadpool_and_no_random_rotation():
    """单线程+抽签退役（用户原则：时限拆除后提速无刚需）。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert "ThreadPoolExecutor" not in src
    assert "1 / 7" not in src and "_rnd" not in src


def test_nominal_time_limit_172800():
    """时限=名义保命值（None 豁免在 billiard 回落 300——双盲审铁证，显式大值唯一路径）。"""
    from src.scheduler import tasks
    assert tasks.dep_audit.soft_time_limit == 172800
    assert tasks.dep_audit.time_limit == 172800


def test_heartbeat_key_ttl_and_done_state():
    """心跳键 TTL 26h（跨 24h 判定窗）+done 终态。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert "26 * 3600" in src
    assert "done" in src


def test_zombie_detection_three_states():
    """入口三态判定（纯逻辑段）：done=正常 / beat 停>10min=zombie / 无键=interrupted。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    assert '"zombie" if age > 600' in src or "'zombie' if age > 600" in src
    assert "interrupted" in src
    assert "zombie" in src


def test_contract_shape_v3():
    """契约瘦身形态：last_complete/os 段/constraints/dev_names/counts；兼容 :rows 键已删（收尾批——表为唯一真源）。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    for frag in ("last_complete", '"os"', "constraints", "dev_names", "prev_state"):
        assert frag in src, frag
    assert ":rows" not in src and "8 * 86400" not in src   # 收尾批：兼容键已删


# ---------------------------------------------------------------------------
# 步4盲审 A/B 同撞 P2 补：mock 全链行为测试（字符串断言放走 P0-1 幽灵 API 的教训）
# ---------------------------------------------------------------------------

def _mock_env(monkeypatch):
    """全链桩：pip 枚举 2 包（1 local）+provider OS 2 包+表读写桩+Valkey 桩。"""
    from unittest.mock import MagicMock
    import src.scheduler.tasks as T
    import src.health_monitor.os_pkg as OP

    installed = [{"name": "pandas", "cur": "2.3.3", "local": False},
                 {"name": "vnpy", "cur": "4.5.0", "local": True}]
    monkeypatch.setattr(OP, "pip_installed", lambda: installed)
    monkeypatch.setattr(OP, "pypi_versions_cached",
                        lambda name, timeout=10: {"versions": ["2.3.3", "3.1.0"], "yanked": []})

    class _P:
        name, version = "openssl", "3.0.1"
    class _Prov:
        id = "dnf"
        def list_installed(self):
            return [_P()]
        def upgrade_candidates(self):
            return {"openssl": {"version": "3.2.0"}}
    monkeypatch.setattr(OP, "detect_provider", lambda: _Prov())

    store = {}
    client = MagicMock()
    client.get.side_effect = lambda k: store.get(k)
    client.set.side_effect = lambda k, v, ex=None: store.__setitem__(k, v)
    monkeypatch.setattr("src.quant_common.redis_client.business_redis", lambda **kw: client)

    sql_calls = []
    class _Cur:
        def executemany(self, sql, rows):
            sql_calls.append((sql, list(rows)))
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, *a):
            return MagicMock(fetchall=lambda: [], fetchone=lambda: None)
    _conn = MagicMock()
    _cur = _Cur()
    _conn.cursor.return_value = _cur
    _conn.commit = lambda: None
    import contextlib
    import src.data_platform.db as DB

    @contextlib.contextmanager
    def _gc():
        yield _conn

    monkeypatch.setattr(DB, "get_conn", _gc)
    return sql_calls, store


def test_full_chain_os_rows_reach_db(monkeypatch):
    """P0-1 回归钉：OS 域经 provider 正门落表（幽灵 API 使 os 恒空的病根防复发）。"""
    import importlib, json
    import src.scheduler.tasks as T
    sql_calls, store = _mock_env(monkeypatch)
    r = T.dep_audit.run()
    assert r["status"] == "ok" and r["n_os"] == 1, r
    os_writes = [c for c in sql_calls if "'system'" in c[0]]
    assert os_writes and os_writes[0][1][0][0] == "openssl", sql_calls
    pip_writes = [c for c in sql_calls if "'pip'" in c[0]]
    assert pip_writes and len(pip_writes[0][1]) == 2


def test_full_chain_beat_done_and_contract(monkeypatch):
    """终态键 done+契约形态（含 os 段非空——B-P0-3 兜底前提）。"""
    import json
    import src.scheduler.tasks as T
    sql_calls, store = _mock_env(monkeypatch)
    r = T.dep_audit.run()
    beat = json.loads(store["sec:dep_audit:beat"])
    assert beat.get("status") == "done" and beat["n"] == 2
    contract = json.loads(store["sec:dep_audit"])
    assert contract["counts"]["n_os"] == 1
    assert contract["os"]["installed"], "os 段非空（P0-1 防复发）"


def test_full_chain_running_mutex(monkeypatch):
    """B-P1-2：beat 活（running）时重入被拒。"""
    import json
    import src.scheduler.tasks as T
    sql_calls, store = _mock_env(monkeypatch)
    # 预置 running beat（新鲜 ts）
    import datetime as dt
    store["sec:dep_audit:beat"] = json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "n": 5})
    r = T.dep_audit.run()
    assert r["status"] == "skipped", r
