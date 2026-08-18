"""健康监控判定：症状型规则 + 触发/恢复沿检测 + health_event 落库 + 告警。

原则（15 号设计）：
- 动作只基于事实信号（unit 状态/心跳存在/依赖可达）；日历只做告警抑制
- 沿检测去重：状态翻转才告警（新触发→告警+落库；恢复→恢复通知），持续态不重复轰炸
- health_monitor 自身写心跳 quant:hb:hm（TTL 120s）——监控死了 Zabbix/外部能看见（互监）
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("health_monitor")

HM_HB_KEY = "quant:hm:health-monitor"      # 自身心跳（beat 任务写）
_STATE_PREFIX = "quant:hm:state:"          # 沿状态：{rule_id} -> "1"/"0"
_NR_PREFIX = "quant:hm:nr:"                # 上次见到的 NRestarts：{unit} -> int

SEV_ORDER = {"critical": 0, "warning": 1, "recovery": 2}


def _notify(severity: str, title: str, body: str) -> None:
    try:
        from src.alert_notify import notify
        notify("critical" if severity == "critical" else "warn", "system", title, body)
    except Exception as e:
        logger.warning("告警发送失败: %s", e)


def _write_event(rule_id: str, component: str, severity: str, detail: str) -> None:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO health_event (rule_id, component, severity, detail) VALUES (%s,%s,%s,%s)",
                (rule_id, component, severity, detail[:500]))
            conn.commit()
    except Exception as e:
        logger.warning("health_event 写入失败: %s", e)


def evaluate(snap: dict) -> list[dict]:
    """规则判定（纯函数，供测试）。返回触发列表：[{rule_id, component, severity, detail}]。"""
    out: list[dict] = []

    # R1 常驻 unit 掉线（事实信号：systemd 状态）
    for unit, st in snap.get("units", {}).items():
        if st and st.get("ActiveState") != "active":
            out.append({"rule_id": "unit_down", "component": unit, "severity": "critical",
                        "detail": f"ActiveState={st.get('ActiveState')} SubState={st.get('SubState')}"})

    # R3 依赖不可达
    deps = snap.get("deps", {})
    for dep in ("postgres", "valkey"):
        if dep in deps and deps[dep] is False:
            out.append({"rule_id": "dep_down", "component": dep, "severity": "critical",
                        "detail": str(deps.get(f"{dep}_err", ""))[:200]})

    # R4 hub 心跳丢失（Valkey 可达但 key 过期 = hub 进程 90s 未续）
    if deps.get("valkey") and snap.get("hub") is None:
        out.append({"rule_id": "hub_hb_lost", "component": "md-hub", "severity": "critical",
                    "detail": "quant:hb:md-hub 不存在（TTL 90s 过期）——hub 进程未续心跳"})

    # R5 任务盲视观测（frozen=1：hub 心跳丢/bar 停更/sticky，worker 已自告警，此处聚合视角降为 warning）
    for tid, t in snap.get("tasks", {}).items():
        if t.get("frozen"):
            out.append({"rule_id": "task_blind", "component": f"task-{tid}", "severity": "warning",
                        "detail": f"frozen=1 md={t.get('md')} lag={t.get('lag')}"})

    return out


def _detect_restarts(snap: dict, r) -> list[dict]:
    """R2 unit 重启沿：NRestarts 比上次快照增长即事件（沿检测，状态翻转不适用——计数器单调）。"""
    events: list[dict] = []
    for unit, st in snap.get("units", {}).items():
        try:
            nr = int(st.get("NRestarts") or 0)
        except (TypeError, ValueError):
            continue
        prev = r.get(f"{_NR_PREFIX}{unit}")
        if prev is not None and nr > int(prev):
            events.append({"rule_id": "unit_restarted", "component": unit, "severity": "warning",
                           "detail": f"NRestarts {prev} -> {nr}"})
        r.set(f"{_NR_PREFIX}{unit}", nr, ex=86400)
    return events


def run_check() -> dict:
    """beat 任务入口（30s）。采集→判定→沿检测→告警/落库→写自身心跳。"""
    from .collector import collect
    snap = collect()
    findings = evaluate(snap)
    new_events: list[dict] = []
    recovered: list[dict] = []
    try:
        from .collector import _valkey
        r = _valkey()
        findings += _detect_restarts(snap, r)

        # 沿检测：以 (rule_id, component) 为键
        current = {(f["rule_id"], f["component"]): f for f in findings}
        for key, f in current.items():
            state_key = _STATE_PREFIX + f["rule_id"] + ":" + f["component"]
            if not r.get(state_key):
                new_events.append(f)
                r.set(state_key, "1", ex=7200)
        # 恢复沿：state 键存在但本次未触发 = 上次问题解除
        for state_key in r.scan_iter(_STATE_PREFIX + "*", count=100):
            token = state_key[len(_STATE_PREFIX):]
            try:
                rule_id, _, component = token.partition(":")
            except ValueError:
                continue
            if (rule_id, component) not in current:
                r.delete(state_key)
                recovered.append({"rule_id": rule_id, "component": component})
        # 自身心跳（供外部/Zabbix 反向监测监控自身）
        r.hset(HM_HB_KEY, mapping={"ts": snap["ts"], "events": len(new_events)})
        r.expire(HM_HB_KEY, 120)
    except Exception as e:
        logger.warning("health_monitor Valkey 操作失败（判定结果照常返回）: %s", e)

    for f in new_events:
        logger.log(logging.CRITICAL if f["severity"] == "critical" else logging.WARNING,
                   "[health] %s %s: %s", f["severity"].upper(), f["component"], f["detail"])
        _notify(f["severity"], f"[health] {f['component']} {f['rule_id']}",
                f"{f['detail']}\nrunbook：15-服务监控设计.md §runbook。")
        _write_event(f["rule_id"], f["component"], f["severity"], f["detail"])
    for rec in recovered:
        logger.info("[health] 恢复: %s %s", rec["component"], rec["rule_id"])
        _notify("recovery", f"[health] 恢复: {rec['component']} {rec['rule_id']}", "")
        _write_event(rec["rule_id"], rec["component"], "recovery", "")

    return {"ts": snap["ts"], "active": len(findings), "new": len(new_events),
            "recovered": len(recovered)}
