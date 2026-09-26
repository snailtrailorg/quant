"""健康监控判定：症状型规则 + 触发/恢复沿检测 + health_event 落库 + 告警。

原则（arch-15设计；2026-08-18 盲审 C/D 修订）：
- 动作只基于事实信号（unit 状态/心跳存在/依赖可达）；日历只做告警抑制
- 沿检测去重：电平型规则（unit_down/dep_down/hub_hb_lost/task_blind）状态翻转才告警；
  计数型规则（unit_restarted）自带沿，绕过电平状态机（D-F4：否则每起重启 30s 后必跟假"恢复"）
- **通知链必须独立于 Valkey 存活**（D-F1）：告警不能与被监控对象共死——Valkey 挂时降级为
  无去重直发（notify 自身已按 SE1 降级继续发）
- 证据缺失 ≠ 证据健康（D-F5）：systemctl 采集失败时跳过 unit_down 判定与恢复扫描
- hub 心跳缺失需连续 2 轮（D-F2：hub 设计内重启窗口 ≥60s，单轮闪断不告警）
- health_monitor 自身写心跳 quant:hm:health-monitor（TTL 120s）——监控死了 Zabbix/外部能看见（互监）
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("health_monitor")

HM_HB_KEY = "quant:hm:health-monitor"      # 自身心跳（beat 任务写）
_STATE_PREFIX = "quant:hm:state:"          # 电平沿状态：{rule_id}:{component} -> "1"
_NR_PREFIX = "quant:hm:nr:"                # 上次见到的 NRestarts：{unit} -> int
_R4_STREAK_KEY = "quant:hm:hub_lost_streak"   # 批 66b：值=JSON dict（per-account streak）
_R6_STALL_KEY = "quant:hm:r6_stall"           # 批 66b：值=JSON dict（per-account）        # 交易时段 sess_ticks 零增长连续轮数
_R6_PREV_KEY = "quant:hm:r6_prev_sess_ticks"  # 批 66b：值=JSON dict（per-account）
EVENT_RETENTION_DAYS = 30


def report_schema_findings(findings: dict) -> None:
    """入口层路由（#48 L-S-C）：verify_schema 纯函数的结果在此转告警/落库——db 层不引告警依赖。

    四入口（web startup / strategy_runner / md_hub / celery 父进程）同报一缺列时，
    notify 的 1min 同标题去重收敛扇出；PG 不可达时 verify_schema 自身已抛，调用方 try 包住。
    """
    if findings.get("expectations_missing"):
        _notify("warning", "[health] schema 校验被禁用",
                "schema_expectations.txt 缺失（部署不完整？）——列级校验未生效。"
                "runbook：重跑链生成命令（db.py load_schema_expectations docstring）并提交。", code="health.schema-off")
        _write_event("schema_drift", "expectations", "warning", "expectations 文件缺失，校验禁用")
        return
    missing_t = findings.get("missing_tables") or []
    missing_c = findings.get("missing_columns") or {}
    if not missing_t and not missing_c:
        return
    detail = f"缺表 {missing_t}；缺列 {missing_c}"
    _notify("critical", "[health] schema 漂移：列级校验发现缺失",
            f"{detail}\nrunbook：alembic upgrade head；升级后仍缺失=该表/列未被迁移链收编，"
            f"参照 0042 模式补收编迁移。", code="health.schema-drift")
    _write_event("schema_drift", "database", "critical", detail)


from src.quant_common.session import in_astock_session as _in_session  # 2026-08-19 归位：删本地复制体


def _notify(severity: str, title: str, body: str, code: str | None = None) -> None:
    """safe_notify 化（P 审：收编三处重复 try/except notify）——critical 走 system 类外推（D-F6）。"""
    from src.alert_notify.notify import safe_notify
    safe_notify("critical" if severity == "critical" else "warn", title, body, code=code)


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


def _prune_events() -> None:
    """保留期清理（D 陷阱 5：health_event 无界增长；crash 循环最坏 ~5.7k 行/天/unit）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM health_event WHERE ts < now() - (%s * interval '1 day')",
                         (EVENT_RETENTION_DAYS,))
            conn.commit()
    except Exception as e:
        logger.debug("health_event 清理失败（表未建/PG 抖动，忽略）: %s", e)


def _prune_metrics() -> None:
    """system_metric 保留期清理（30 天，与 health_event 同款）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM system_metric WHERE ts < now() - (%s * interval '1 day')",
                         (EVENT_RETENTION_DAYS,))
            conn.commit()
    except Exception as e:
        logger.debug("system_metric 清理失败（表未建/PG 抖动，忽略）: %s", e)


def _hub_expected_ids() -> list[str]:
    """批 66b：R4 期望集（DB 行=缺席发现唯一正确来源——SCAN 在场测不到缺席；低频 30s 轻查询，
    不入 collect〔/metrics 高频〕）。provider 白名单过滤同 SA4/deploy（防未实现行误报缺失）；
    查询失败返回 []（evaluate 降级空集地板判定）。"""
    try:
        from src.data_platform.db import get_conn
        from src.strategy_framework.md_gateway import list_md_gateway_providers
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id FROM external_interface "
                "WHERE enabled=true AND 'trading' = ANY(capabilities) "
                "AND provider = ANY(%s)", (list(list_md_gateway_providers()),))
            return [str(x[0]) for x in cur.fetchall()]
    except Exception as e:
        logger.warning("R4 期望集查询失败（降级空集地板判定）: %s", e)
        return []


def evaluate(snap: dict, state: dict | None = None) -> tuple[list[dict], dict]:
    """规则判定（纯函数，供测试）。返回 (findings, state)——state 为跨轮状态字典，
    由调用方持久化（R4/R6 需要连续轮次证据）。"""
    # 批 66b：R4/R6 per-account 化（hub N 实例）——streak/prev 均为 {account_id: n} dict
    state = dict(state or {"hub_lost_streak": {}, "sess_stall": {}, "prev_sess_ticks": {}})
    out: list[dict] = []

    # R1 常驻 unit 掉线（事实信号：systemd 状态）
    # D-F2：SubState=auto-restart 是 systemd 设计内自愈（RestartSec 窗口），由 R2 计数沿报告，跳过
    for unit, st in snap.get("units", {}).items():
        if st and st.get("ActiveState") != "active" and st.get("SubState") != "auto-restart":
            out.append({"rule_id": "unit_down", "component": unit, "severity": "critical",
                        "detail": f"ActiveState={st.get('ActiveState')} SubState={st.get('SubState')}"})

    # R3 依赖不可达
    deps = snap.get("deps", {})
    for dep in ("postgres", "valkey"):
        if dep in deps and deps[dep] is False:
            out.append({"rule_id": "dep_down", "component": dep, "severity": "critical",
                        "detail": str(deps.get(f"{dep}_err", ""))[:200]})

    # R7 长事务预警（arch-18 §4.2，DB 优化批 2026-08-21）：idle in transaction >3min——
    # 参数防线（idle_in_tx 5min）杀掉前的预警窗，留人工 pg_terminate 介入（诊断钥匙见 arch-18 §4.3）
    stale_tx = snap.get("db_idle_tx_stale")
    if stale_tx:
        out.append({"rule_id": "db_idle_tx_stale", "component": "postgres", "severity": "warning",
                    "detail": f"{stale_tx} 个 idle in transaction 事务超 3 分钟（防线 5min 兜杀，"
                              f"诊断：arch-18 §4.3 pg_stat_activity）"})

    # R4 hub 心跳丢失（批 66b N 实例：期望集×在场集交叉——run_check 注入 snap["hub_expected"]，
    # DB 行是缺席发现的唯一正确来源〔SCAN 在场发现天然测不到缺席〕；期望集缺供=空集地板降级判定）
    # D-F2：需连续 2 轮——hub 设计内重启（deploy/自愈）首跳心跳要 60s+，单轮闪断不告警
    hubs = snap.get("hubs") or {}
    expected: list = snap.get("hub_expected") or []
    streaks = state["hub_lost_streak"]
    if deps.get("valkey"):
        for acct in expected:
            missing = int(acct) not in hubs
            streaks[acct] = (streaks.get(acct, 0) + 1) if missing else 0
            if missing and streaks[acct] >= 2:
                out.append({"rule_id": "hub_hb_lost", "component": f"md-hub:{acct}", "severity": "critical",
                            "detail": f"quant:hb:md-hub:{acct} 连续 {streaks[acct]} 轮（30s/轮）缺失——hub 进程未续心跳"})
        if not expected:
            # 空集地板（期望集未注入=交叉不可用）：在场集非空即过，全空按全局 streak（保守告警）
            missing = not hubs
            streaks["__all__"] = (streaks.get("__all__", 0) + 1) if missing else 0
            if missing and streaks["__all__"] >= 2:
                out.append({"rule_id": "hub_hb_lost", "component": "md-hub", "severity": "critical",
                            "detail": f"在场集空且期望集未注入，连续 {streaks['__all__']} 轮——hub 心跳全缺失"})

    # R6 交易时段 tick 停滞（批 66b per-account；盲审 C-S1：删自杀后"半开连接/线程挂死但
    # 主循环活着"只剩告警抓手）
    if _in_session():
        prevs = state["prev_sess_ticks"]
        stalls = state["sess_stall"]
        for acct, hub in hubs.items():
            # E-3：哨兵用 None 判缺失——`or -1` 会把合法值 0 钳成 -1（hub 时段中重启 sess_ticks=0
            # 恰是 R6 目标场景，曾永远累加不起来）
            _prev = prevs.get(acct)
            prev = int(_prev) if _prev is not None else -1
            stalled = prev >= 0 and prev == hub["sess_ticks"]
            stalls[acct] = (stalls.get(acct, 0) + 1) if stalled else 0
            if stalls[acct] >= 2:   # ≥2 轮（约 60-90s）零增长，时段内正常 cadence ~3s/tick
                out.append({"rule_id": "hub_tick_stalled", "component": f"md-hub:{acct}", "severity": "critical",
                            "detail": f"交易时段 account {acct} sess_ticks 零增长持续 {stalls[acct]} 轮"
                                      f"（prev={prev} cur={hub['sess_ticks']}）——疑似半开连接/线程挂死，"
                                      f"runbook：journalctl 查 tick；确认后手动 restart（worker 自动暖机）"})
            prevs[acct] = hub["sess_ticks"]

    # R5 任务盲视观测（frozen=1：worker 已自告警，此处聚合视角降为 warning）
    for tid, t in snap.get("tasks", {}).items():
        if t.get("frozen"):
            out.append({"rule_id": "task_blind", "component": f"task-{tid}", "severity": "warning",
                        "detail": f"frozen=1 md={t.get('md')} lag={t.get('lag')}"})

    # R8-R10 系统资源阈值（mem/disk/swap；阈值配置驱动 snap["thresholds"]，缺省回落硬编码；
    #   各指标独立采集（周期不同）——某 kind None=证据缺失，只跳过该 kind）
    res = snap.get("resources")
    if res:
        thr = snap.get("thresholds") or {"mem": {"warn": 0.6, "crit": 0.9},
                                         "disk": {"warn": 0.8, "crit": 0.9},
                                         "swap": {"warn": 0.8}}
        for kind, comp, rule in (("mem", "memory", "mem_high"), ("swap", "swap", "swap_high")):
            r = res.get(kind)
            if not r or r.get("pct") is None:
                continue
            pct = r["pct"]
            sev = "critical" if (thr[kind].get("crit") is not None and pct > thr[kind]["crit"]) \
                else ("warning" if pct > thr[kind]["warn"] else None)
            if sev:
                out.append({"rule_id": rule, "component": comp, "severity": sev,
                            "detail": f"{kind} used {pct:.1%}（阈值 {sev}）"})
        # R9 磁盘逐挂载点判定（聚合求和会稀释单分区爆满——盲审 A-P1；component 带路径）
        dthr = thr["disk"]
        for p in (res.get("disk") or {}).get("paths") or []:
            pct = p.get("pct")
            if pct is None:
                continue
            sev = "critical" if (dthr.get("crit") is not None and pct > dthr["crit"]) \
                else ("warning" if pct > dthr["warn"] else None)
            if sev:
                out.append({"rule_id": "disk_high", "component": f"disk:{p['path']}", "severity": sev,
                            "detail": f"disk {p['path']} used {pct:.1%}（阈值 {sev}）"})

    return out, state


def _detect_restarts(snap: dict, r) -> list[dict]:
    """R2 unit 重启沿：NRestarts 比上次增长即事件。计数器单调 → 自带沿，
    返回的事件由调用方直发（绕过电平状态机，D-F4）。"""
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
    """beat 任务入口（30s）。采集→判定→沿检测→告警/落库→写自身心跳。

    结构约束（D-F1）：通知循环在最外层，任何存储故障都跳过它上面的所有 try 继续到达。
    """
    from .collector import collect, _valkey
    snap = collect()
    snap["hub_expected"] = _hub_expected_ids()
    state = {"hub_lost_streak": 0, "sess_stall": 0, "prev_sess_ticks": None}
    new_events: list[dict] = []
    recovered: list[dict] = []
    valkey_ok = False
    r = None

    try:
        r = _valkey()
        # 载入跨轮状态（批 66b：JSON dict per-account；坏值/旧标量形态按空 dict 从零起）
        import json as _json
        def _load_dict(key):
            try:
                v = r.get(key)
                d = _json.loads(v) if v else {}
                return d if isinstance(d, dict) else {}
            except Exception:
                return {}
        state["hub_lost_streak"] = _load_dict(_R4_STREAK_KEY)
        state["sess_stall"] = _load_dict(_R6_STALL_KEY)
        state["prev_sess_ticks"] = _load_dict(_R6_PREV_KEY)
        valkey_ok = True
    except Exception as e:
        logger.warning("health_monitor Valkey 状态载入失败（本轮按无历史状态判定）: %s", e)

    findings, state = evaluate(snap, state)   # 纯判定一次（E 简化：去掉双重 evaluate）

    if valkey_ok:
        try:
            # D-F4（E-2 实锤重修）：计数沿事件直发且**完全绕过电平状态机**——不设 state 键、
            # 不参与恢复扫描（此前只是并进 findings，30s 后必跟假"恢复"，实测复现）
            restart_events = _detect_restarts(snap, r)
            findings += restart_events
            new_events += restart_events

            current = {(f["rule_id"], f["component"]): f for f in findings
                       if f["rule_id"] != "unit_restarted"}
            for key, f in current.items():
                state_key = _STATE_PREFIX + f["rule_id"] + ":" + f["component"]
                # state 键值存 severity：首触发 / warn→critical 升级 = 新沿（重发通知）；
                # 降级（critical→warn）只更新键值不重发（盲审 A-P3）；旧值 "1" 过渡=视为已触发
                prev = r.get(state_key)
                if not prev or (prev != f["severity"] and f["severity"] == "critical"):
                    new_events.append(f)
                    r.set(state_key, f["severity"], ex=7200)
                elif prev != f["severity"]:
                    r.set(state_key, f["severity"], ex=7200)
            # 恢复沿：state 键在而本次未触发。D-F5：采集失败时对应规则不判恢复（证据缺失≠恢复）
            units_evidence = bool(snap.get("units"))
            _res = snap.get("resources") or {}
            res_evidence = {"mem_high": _res.get("mem") is not None,
                            "disk_high": _res.get("disk") is not None,
                            "swap_high": _res.get("swap") is not None}
            for state_key in r.scan_iter(_STATE_PREFIX + "*", count=100):
                token = state_key[len(_STATE_PREFIX):]
                rule_id, _, component = token.partition(":")
                if rule_id == "unit_down" and not units_evidence:
                    continue
                if rule_id in res_evidence and not res_evidence[rule_id]:
                    continue
                if (rule_id, component) not in current:
                    r.delete(state_key)
                    recovered.append({"rule_id": rule_id, "component": component})
            # 写回跨轮状态 + 自身心跳（供外部/Zabbix 反向监测监控自身）
            r.set(_R4_STREAK_KEY, _json.dumps(state["hub_lost_streak"]), ex=7200)
            r.set(_R6_STALL_KEY, _json.dumps(state["sess_stall"]), ex=7200)
            r.set(_R6_PREV_KEY, _json.dumps(state["prev_sess_ticks"]), ex=7200)
            r.hset(HM_HB_KEY, mapping={"ts": snap["ts"]})
            r.expire(HM_HB_KEY, 120)
        except Exception as e:
            logger.warning("health_monitor Valkey 操作失败（降级无去重直发本轮全部判定）: %s", e)
            new_events = findings   # 存储中途挂：本轮全部判定直发，去重放弃（D-F1）
    else:
        # D-F1 核心：Valkey 完全不可达时也要把判定发出去（dep_down(valkey) 本身就是最紧急的事件）
        new_events = findings   # 无存储=无沿检测，无去重直发

    for f in new_events:
        logger.log(logging.CRITICAL if f["severity"] == "critical" else logging.WARNING,
                   "[health] %s %s: %s", f["severity"].upper(), f["component"], f["detail"])
        _notify(f["severity"], f"[health] {f['component']} {f['rule_id']}",
                f"{f['detail']}\nrunbook：15-服务监控设计.md §runbook。", code="health.component")
        _write_event(f["rule_id"], f["component"], f["severity"], f["detail"])
    for rec in recovered:
        logger.info("[health] 恢复: %s %s", rec["component"], rec["rule_id"])
        _notify("recovery", f"[health] 恢复: {rec['component']} {rec['rule_id']}", "", code="health.recovery")
        _write_event(rec["rule_id"], rec["component"], "recovery", "")

    # system_metric 采样（每 60s 落一行=最小采集周期；epoch 取模，重启免疫、免状态键；
    #   各指标独立采集（周期不同）——未采集的 kind 落 NULL，series 查询端按行读各自列即可）
    res = snap.get("resources")
    if res and int(snap["ts"]) % 60 < 30:
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO system_metric "
                    "(ts, mem_total, mem_used, swap_total, swap_used, disk_total, disk_used) "
                    "VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (ts) DO NOTHING",
                    (snap["ts"],
                     (res.get("mem") or {}).get("total"), (res.get("mem") or {}).get("used"),
                     (res.get("swap") or {}).get("total"), (res.get("swap") or {}).get("used"),
                     (res.get("disk") or {}).get("total"), (res.get("disk") or {}).get("used")))
                conn.commit()
        except Exception as e:
            logger.warning("system_metric 写入失败: %s", e)

    if int(snap["ts"]) % 86400 < 60:   # 每日一轮清理（epoch 取模，随 beat 周期命中一次）
        _prune_events()
        _prune_metrics()

    return {"ts": snap["ts"], "active": len(findings), "new": len(new_events),
            "recovered": len(recovered)}
