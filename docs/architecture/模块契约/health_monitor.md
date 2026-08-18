# 模块契约 · health_monitor（健康监控，15 号设计）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件。
> 配套：`docs/architecture/15-服务监控设计.md`（设计全貌）+ `docs/architecture/接口契约.md`。

## 职责
双层监控的内层：采集组件状态（systemd unit/Valkey 心跳族/依赖）→ 症状型规则判定 →
触发/恢复沿检测 → health_event 落库 + 飞书告警。自身写心跳供 Zabbix 外层反向监测（互监）。
原则：动作只基于事实信号；日历只做告警抑制；通知链独立于 Valkey 存活（D-F1）。

## 文件结构
```
server/src/health_monitor/
├── __init__.py
├── collector.py   # collect() 快照 + render_prometheus() + systemctl_units
└── monitor.py     # evaluate() 规则 + run_check() beat 入口 + report_schema_findings() 入口路由
```

---

## 一、public API（稳定，可跨模块调用）

### collector.py
```python
CORE_UNITS: list[str]          # 5 个常驻 unit（web-api/celery-worker/beat/risk/md-hub，实例 @quant）
systemctl_units(units) -> dict  # 批量 ActiveState/SubState/NRestarts；失败返回 {}（=证据缺失）
collect(now=None) -> dict      # 幂等快照 {ts, units, deps, hub, tasks, valkey_memory}；子项失败不拖垮整体
render_prometheus(snap) -> str # Prometheus 文本（按指标族分组——严格解析器兼容）
```

### monitor.py
```python
evaluate(snap, state=None) -> tuple[list[dict], dict]
    # 纯函数规则判定；state={"hub_lost_streak","sess_stall","prev_sess_ticks"} 由调用方持久化
    # 规则：R1 unit_down（auto-restart 豁免）/R2 unit_restarted（计数沿，绕过电平状态机）
    #      R3 dep_down / R4 hub_hb_lost（连续 2 轮）/ R5 task_blind（warning）/ R6 hub_tick_stalled（时段内零增长≥2 轮）
run_check() -> dict            # beat 30s 入口（risk 队列，expires=25）：采集→判定→沿检测→告警/落库→自身心跳
report_schema_findings(findings) -> None
    # #48 入口路由：verify_schema 纯函数结果 → 告警/health_event（db 层不引告警依赖）
    # expectations_missing 哨兵 → warning"校验被禁用"
```

### 依赖（本模块 import 谁）
stdlib only（模块级）；`redis`/`src.data_platform.db`/`src.alert_notify.notify` 全部**函数内延迟 import**
（守卫：db 层反向只经 report_schema_findings 单点，不构成环）。

## 二、被调（谁 import 本模块）
- `web_api/main.py`：/metrics /readyz /api/health/components /api/health/events + startup 的 schema 校验
- `scheduler/tasks.py`：`health_monitor_check` beat 任务 → run_check
- `scheduler/app.py`：celery 父进程 schema 校验（import 期一次）
- `strategy_runner/main.py`、`md_hub/main.py`：启动 schema 校验
- `/metrics` 消费方（外部）：Zabbix HTTP agent（Phase 2）/ Prometheus / Grafana

## 三、读写表
- **写**：`health_event`（rule_id/component/severity/detail；30 天保留，每日 prune）
- **读**：无 PG 业务表（collector 只探活 SELECT 1）

## 四、Valkey 键
| 键 | 语义 |
|---|---|
| `quant:hm:health-monitor` | 自身心跳（TTL 120s；外层监测"监控死了"用） |
| `quant:hm:state:{rule}:{component}` | 电平沿状态（TTL 7200） |
| `quant:hm:nr:{unit}` | NRestarts 上次值（计数沿，TTL 86400） |
| `quant:hm:hub_lost_streak` / `r6_stall` / `r6_prev_sess_ticks` | R4/R6 跨轮证据 |

## 五、不变量
1. **通知链独立于存储**：run_check 的通知循环在最外层，Valkey 挂 → 无去重直发（dep_down(valkey) 本身就是最紧急事件）
2. **证据缺失 ≠ 证据健康**：units 采集失败 → 跳过 R1 判定与恢复扫描
3. **计数沿不进电平状态机**（D-F4）：unit_restarted 直发，30s 后不发假"恢复"
4. evaluate 纯函数（可测）；副作用只在 run_check/入口路由
