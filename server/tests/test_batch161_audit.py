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
    """契约瘦身形态：last_complete/os 段/constraints/dev_names/counts；兼容 :rows 键 TTL 8d。"""
    import inspect
    from src.scheduler import tasks
    src = inspect.getsource(tasks.dep_audit)
    for frag in ("last_complete", '"os"', "constraints", "dev_names", "prev_state"):
        assert frag in src, frag
    assert ":rows" in src and "8 * 86400" in src
