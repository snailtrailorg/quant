"""健康采集：一次 collect() 得到全组件快照。/metrics 端点与 health_monitor beat 任务共用。

设计要点：
- 任何子项失败只置 False/空，不拖垮整体（采集自身必须比被监控者更皮实）
- systemd 读取用 systemctl show 批量（非 systemd 环境=本地/CI 返回空，指标缺省而非报错）
- 任务单元（live-task@*）按需启停不入常驻清单，其健康走 Valkey 心跳动态发现
"""
from __future__ import annotations

import os
import subprocess
import time

# 常驻单元（实例=quant）。live-task@*/strategy@* 按需，心跳覆盖；feishu 多实例动态发现
CORE_UNITS = [
    "quant-web-api@quant",
    "quant-celery-worker@quant",
    "quant-celery-beat@quant",
    "quant-celery-risk@quant",
    "quant-md-hub@quant",
]

HUB_HB_KEY = "quant:hb:md-hub"
TASK_HB_PATTERN = "quant:hb:task:*"


def _valkey():
    import redis
    return redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                decode_responses=True, socket_timeout=2)


def systemctl_units(units: list[str]) -> dict:
    """批量取 ActiveState/SubState/NRestarts。返回 {unit: {...}}；非 systemd 环境返回空 dict。

    注意（盲审 D-F5）：返回空 = 采集失败/无证据，调用方必须区分"证据缺失"与"证据健康"，
    不能据此判 unit 全健康或清 unit_down 恢复沿。
    """
    if not units:
        return {}
    try:
        out = subprocess.run(
            ["systemctl", "show", *units,
             "-p", "Id", "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts"],
            capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return {}
        result: dict = {}
        cur = None
        for line in out.stdout.splitlines():
            k, _, v = line.partition("=")
            if k == "Id":
                cur = v
                result[cur] = {}
            elif cur is not None and k in ("ActiveState", "SubState", "NRestarts"):
                result[cur][k] = v
        return result
    except Exception:
        return {}


def collect(now: float | None = None) -> dict:
    """单次快照：units + 依赖 + hub/任务心跳。幂等无副作用，可被 /metrics 高频调用。"""
    now = now if now is not None else time.time()
    snap: dict = {"ts": now, "units": {}, "deps": {}, "hub": None, "tasks": {}}

    snap["units"] = systemctl_units(CORE_UNITS)

    try:
        r = _valkey()
        r.ping()
        snap["deps"]["valkey"] = True
        # hub 心跳（key 存在=进程 90s 内活着；last_tick_ts 是数据新鲜度，另列）
        h = r.hgetall(HUB_HB_KEY)
        if h:
            last_tick = float(h.get("last_tick_ts") or 0)
            snap["hub"] = {
                "gen": int(h.get("gen") or 0),
                "subs": int(h.get("subs") or 0),
                "ticks": int(h.get("ticks") or 0),
                "sess_ticks": int(h.get("sess_ticks") or 0),
                "bars": int(h.get("bars") or 0),
                "dropped_pg": int(h.get("dropped_pg") or 0),
                "tick_age": (now - last_tick) if last_tick else None,
            }
        # 任务心跳（动态发现；key TTL 90s，存在=活）
        for key in r.scan_iter(TASK_HB_PATTERN, count=100):
            tid = key.rsplit(":", 1)[-1]
            try:
                t = r.hgetall(key)
            except Exception:
                continue
            if t:
                lag = float(t.get("lag") or 0)
                snap["tasks"][tid] = {
                    "md": t.get("md", "direct"),
                    "bars": int(t.get("bars") or 0),
                    "lag": lag if lag >= 0 else None,
                    "frozen": int(t.get("frozen") or 0),
                }
        try:
            snap["valkey_memory"] = int(r.info("memory").get("used_memory") or 0)
        except Exception:
            pass
    except Exception as e:
        snap["deps"]["valkey"] = False
        snap["deps"]["valkey_err"] = str(e)[:80]

    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        snap["deps"]["postgres"] = True
    except Exception as e:
        snap["deps"]["postgres"] = False
        snap["deps"]["postgres_err"] = str(e)[:80]

    return snap


def render_prometheus(snap: dict) -> str:
    """快照 → Prometheus 文本格式（text/plain; version=0.0.4）。业界交换标准，
    Zabbix HTTP agent + Prometheus pattern 预处理 / Grafana / Prometheus 通吃。

    盲审 D-F3（2026-08-18）：按指标族分组输出（HELP/TYPE 每族一组且在所有样本之前）——
    逐行穿插去重会产生重复 HELP 行，严格解析器（Prometheus/OpenMetrics）会整个 target 拒收。
    """
    families: dict[str, list[str]] = {}   # metric -> [HELP 行, TYPE 行, ...样本]
    order: list[str] = []

    def emit(metric: str, value, help_text: str, labels: dict | None = None,
             mtype: str = "gauge") -> None:
        if metric not in families:
            families[metric] = [f"# HELP {metric} {help_text}", f"# TYPE {metric} {mtype}"]
            order.append(metric)
        if labels:
            lab = ",".join(f'{k}="{v}"' for k, v in labels.items())
            families[metric].append(f"{metric}{{{lab}}} {value}")
        else:
            families[metric].append(f"{metric} {value}")

    def b(v) -> int:
        return 1 if v else 0

    for unit, st in snap.get("units", {}).items():
        emit("quant_unit_up", b(st.get("ActiveState") == "active"), "systemd unit active", {"unit": unit})
        try:
            emit("quant_unit_nrestarts", int(st.get("NRestarts") or 0), "unit restart count", {"unit": unit})
        except (TypeError, ValueError):
            pass
    for dep, ok in snap.get("deps", {}).items():
        if isinstance(ok, bool):
            emit("quant_dep_up", b(ok), "dependency reachable", {"dep": dep})
    if "valkey_memory" in snap:
        emit("quant_valkey_memory_bytes", snap["valkey_memory"], "valkey used memory")

    hub = snap.get("hub")
    emit("quant_hub_hb_present", b(hub is not None), "md-hub heartbeat key present (TTL 90s)")
    if hub:
        emit("quant_hub_gen", hub["gen"], "hub generation (fencing)")
        emit("quant_hub_subs", hub["subs"], "subscribed symbols")
        emit("quant_hub_ticks_total", hub["ticks"], "ticks since process start", mtype="counter")
        emit("quant_hub_sess_ticks_total", hub["sess_ticks"], "ticks within current session", mtype="counter")
        emit("quant_hub_bars_total", hub["bars"], "bars since process start", mtype="counter")
        emit("quant_hub_dropped_pg_total", hub["dropped_pg"], "bars dropped by PG writer", mtype="counter")
        if hub["tick_age"] is not None:
            emit("quant_hub_tick_age_seconds", round(hub["tick_age"], 1), "seconds since last tick (wall)")

    for tid, t in snap.get("tasks", {}).items():
        emit("quant_task_up", 1, "task heartbeat key present", {"task": tid, "md": t["md"]})
        emit("quant_task_bars_total", t["bars"], "bars consumed", {"task": tid}, mtype="counter")
        emit("quant_task_frozen", t["frozen"], "frozen flag (observability)", {"task": tid})
        if t["lag"] is not None:
            emit("quant_task_lag_seconds", round(t["lag"], 1), "seconds since last bar", {"task": tid})

    lines: list[str] = []
    for metric in order:
        lines.extend(families[metric])
    return "\n".join(lines) + "\n"
