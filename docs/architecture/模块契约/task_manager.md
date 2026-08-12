# 模块契约 · task_manager（后台任务管理）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（§4 Task 跨模块签名）。本文件不重复签名定义，只列"本模块暴露什么"。

## 职责
统一后台任务管理：`tasks` + `task_logs` 两表的 CRUD + 心跳 + 卡死检测 + 失败告警。
**所有异步任务（回测/同步/AI/策略）调 `create_task` + `update_heartbeat` + `complete_task` 即纳入统一管理 + 卡死检测 + 告警**（PT1 平台化核心）。

## 文件结构
```
server/src/task_manager.py   # 单文件，10 个模块级函数（非类）
```

---

## 一、public API（稳定，可跨模块调用）

> 签名详见接口契约 §4 Task。以下只补"看代码看不出的行为约定"。

```python
create_task(task_id, name, task_type, trigger_type, trigger_user, params=None) -> None
    # INSERT ... ON CONFLICT (id) DO UPDATE（可重试，幂等）
    # status='running'，progress 初始化 {current:0,total:0,pct:0,step:""}
    # task_type: backtest/sync/ai/trade/strategy；trigger_type: manual/schedule/event
    # 关联：backtest_runs.task_id / live_task.task_id 指向 tasks.id
update_heartbeat(task_id, progress=None) -> None
    # progress=None 只刷 last_heartbeat；有值则同步写 progress JSON
complete_task(task_id, status="completed", error=None) -> None
    # 写 end_time + error_message；status: completed/failed/terminated
log_task(task_id, level, message, step_name=None, sql_or_api=None) -> None
    # 写 task_logs；level: INFO/WARN/ERROR/DEBUG；step_name/sql_or_api 供故障定位
list_tasks(status=None, limit=100) -> list[dict]
    # 按 updated_at DESC；progress 字段 JSON 反序列化；不含 logs
get_task(task_id) -> dict | None
    # 含最近 50 条 task_logs（created_at DESC）；params 字段 JSON 反序列化；无则 None
terminate_task(task_id) -> None
    # status='terminated' + end_time；⚠️ 不 kill 进程（pid 由调用方处理）
force_delete_task(task_id) -> None
    # 删 task_logs + tasks（卡死清理用）
detect_stuck(timeout_s=300) -> int
    # status='running' AND last_heartbeat < now()-timeout -> 'stuck'；RETURNING 计数 + warning 日志
notify_on_failure(title, body, provider="wechat_work") -> None
    # PT7 跨层联动：lazy import alert_notify.channel.get_channel -> ch.send(level="error")
    # 失败不抛（只 warning 日志）
```

---

## 二、内部 API（不保证稳定）

- 无独立内部函数；所有函数都是 public。`logger` 是模块级 `logging.getLogger("task_manager")`

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| task_manager.py | `data_platform.db.get_conn`（顶部 import） | 所有 SQL 走连接池 |
| task_manager.py | `alert_notify.channel.get_channel`（**函数内 lazy import**，仅 `notify_on_failure`） | 失败告警，避免顶层循环依赖 |

> 无循环依赖风险：本模块只被 scheduler/web_api 调，不反向依赖它们。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `scheduler.tasks`（同步任务编排） | `create_task` / `update_heartbeat` / `complete_task` / `log_task` / `notify_on_failure` |
| `scheduler.tasks`（定时巡检） | `detect_stuck`（cron 触发） |
| `scheduler.tasks`（回测 dispatch） | `create_task` / `update_heartbeat` / `complete_task` |
| `web_api.main`（任务管理端点） | `get_task` / `list_tasks` / `terminate_task` / `force_delete_task` / `detect_stuck` |

> web_api 端点：`list_tasks_api` / `get_task_api` / `terminate_task_api` / `force_delete_task_api` / `detect_stuck_api`（均 admin 权限）。`terminate_task_api` 调完 `terminate_task` 还补一条 `log_task(WARN)`。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `tasks` | `create_task`（INSERT）/ `update_heartbeat` / `complete_task` / `terminate_task` / `force_delete_task` / `detect_stuck`（UPDATE） | `list_tasks` / `get_task` |
| `task_logs` | `log_task`（INSERT） | `get_task`（最近 50 条） |

> 表 schema：migration 0014。`tasks` 主键 `id`(string64，调用方生成)；含 `pid`/`progress`(JSON)/`params`(JSON)/`last_heartbeat`/`error_message`。`task_logs` 含 `step_name`/`sql_or_api`/`resource_usage`(JSON) 供故障定位。索引：`ix_tasks_status` / `ix_task_logs_task_id`。

---

## 六、不变量

- **task_id 主键**：string(64)，调用方生成（UUID 或业务 id 如 `sync_xxx` / `backtest_run_N`）
- **create_task 幂等**：ON CONFLICT DO UPDATE（同 id 重试不报错，重置 running + 心跳）
- **progress JSON schema**：`{current:int, total:int, pct:int, step:str}`（list/get 反序列化为 dict）
- **status 枚举**：`running` / `paused` / `completed` / `failed` / `terminated` / `stuck`
- **terminate vs force_delete**：terminate 只改 status（记录保留）；force_delete 物理删（卡死清理）
- **detect_stuck 只标记不清理**：进程 kill 由调用方（有 pid 时）；本函数只 UPDATE status='stuck'
- **notify_on_failure 容错**：通道失败只 warning，不抛（不阻塞任务流程）
- **连接池**：每个函数独立 `with get_conn()` + `commit()`（无跨函数事务）

---

## 七、扩展指南

### 新任务类型纳入管理
1. 任务入口调 `create_task(task_id, name, task_type, ...)`（task_type 用新值如 `"strategy"`）
2. 执行中周期调 `update_heartbeat(task_id, progress)` + `log_task`（关键步骤）
3. 结束调 `complete_task(task_id, status, error)`；失败额外 `notify_on_failure`
4. 不改本模块（task_type 是自由字符串，无枚举校验）

### 进程 kill（terminate 当前不实现）
- `terminate_task` 只改 status；若要 kill 进程，调用方读 `tasks.pid` 后自行 `kill`/`systemctl stop`（当前无调用方实现，TODO）

---

## 修订记录
- 2026-08-10 初版（基于代码核实：task_manager.py 全读 + 被调 grep web_api/scheduler）
