# 批 66 · D26-B hub 拓扑迁移（账号级实例化 + M5 退役 + 观测面 N 化）

> 任务序列第 5 棒。方案真源 `docs/design/D26-市场接入架构.md` v5（§五 13 项清单为本批核心交付）。
> 勘察基线 2026-09-26（HEAD f0bba2b）——13 项锚点逐项核实，两处文档锚点错置已在本文修正（§0.3）。
> **结构（用户裁定 2026-09-26）：先整体方案与接口/边界/约束/职责（§1-§3，三批共用契约层），再分批展开（§4）。**
> **v2（2026-09-26）：方案双盲 A（金融 P0×2+P1×6）/ B（架构 P0×3+P1×5）全处置**——处置记录 §6。v1 缺陷集中在「方案文字正确但管道/特权通道/payload 契约的现实不支撑」。

---

## 0. 前置摘要

### 0.1 目标（一句话）

一行 trading 域 `external_interface` = 一个 hub（unit `quant-md-hub@{account_id}`）+ 一套 TD 会话；**全键 per-account 化**（流/心跳/水位/latest_tick 四键族）+ **流消息 payload 全市场带 account_id**；M5 互备退役；观测面从 1→N；期望源=DB 行。

### 0.2 依赖（就绪）

- D26-A 批 65 ✅（TD 注册表框架——本批不动 TD 侧；A 审核实 TD 侧零波及，live_task.account_id 与 unit 名同源于 external_interface.id 且 NOT NULL+FK 一致有保证）
- ST6 容量结论 ✅（批 65b：hub 模式 worker ≥86M/任务下界，现配可容）
- 迁移 0100/0104 ✅（trading 域行 row_id 必填已由 DB 完成，无回填）
- 现网形态=1 A 股账号+1 任务（D26 §五「迁移时机成本最低」判定成立）

### 0.3 勘察修正（D26 文档锚点 vs 实际代码；双盲两审逐项抽查属实）

| D26 原文 | 实际 | 处置 |
|---|---|---|
| #6「旧 --id 启动路径 main.py:449-461」 | md_hub/main.py 仅 437 行无 argparse；旧 --id 路径在 **strategy_runner/main.py:288（argparse）/323-325（缺参退出）/367-404（旧架构分支，390-404 min(id) 默认行兜底）** | 退役对象=worker 旧路径（worker 侧），非 md_hub |
| #6「broker.py:207」 | `get_interface_row` 在 **strategy_framework/broker.py:214**（缺省分支 SQL :234-238） | 行号修正 |
| #5「/readyz hub_hb system.py:114」 | :114 精确命中，但检查挂在 **/api/_probe**（路由 :66，deploy 阶段 8 探针消费）；真 /readyz（:140-152）只查 pg/valkey | 语义裁定落地位置=/api/_probe 的 hub_hb 检查 |
| §4.1「退出码 5 唯一生产者=续租丢路径已死」 | 续租 CAS 败→exit(5) 在产（main.py:362）；a′ **保留**续租原语→5 生产者不死 | 本文 §1.4 重新裁定（5 退役、生产者改 exit(1)） |
| §4.2「hub_worker.py:200-201 buy_ok 门」 | 精确命中（trading.py:32-45 纯函数+main.py:225-242 网关） | 附加发现：`in_session=_in_astock_session()` 对加密任务误伤（§0.4-1） |
| §4.1 退出码矩阵 exit 9 行 | 现状 9 **不在** Prevent——RestartSec=30 自动重拉重读行=D26「~30s 轻」承诺的载体；v1 误写「Prevent+SA4 拉回」（SA4 backoff 可至 1h=劣化一个量级） | **9 维持不进 Prevent**（B-P1-3） |

### 0.4 勘察新增事实（D26 未列名，本批收编）

1. **worker 时段门误伤**（hub_worker.py:202/238）：`_in_astock_session()` 对加密 24/7 任务同样生效——A 股盘外 BUY 被误拒+STALE_PUB_S 丢弃误前置。加密未开闸所以无症状，属「分支税」未列名实例 → 66c 修。
2. **两侧键判定谓词不同源**：worker=`_crypto_provider(symbol)`（symbol 后缀）；hub=接口行 `market` 字段。66b 统一为 **account_id 单一谓词**（symbol 后缀判定退役）。
3. **latest_tick 加密写读分叉**：写侧已 account 化（parts.py:317），读者裸 GET（stock_detail.py:168）——加密标的今天无人能读（无消费者无症状）→ 66b 读者改 SCAN 契约。
4. **`deploy_hub_hb_redis_key` 死配置**：三套 group_vars 定义（quant-prod:65-66 等），playbooks/wrappers 全仓零引用（wrapper 只认 env `QUANT_HB_KEY`/默认值）→ 66b 清理。
5. **switch.py `diff` 子命令名存实亡**（main() 无 diff 分支，落 exit 5）→ 随 M5 整体退役消失；手册 `server/docs/manual/05-数据源切换.md` 随批重写。
6. **`_hub_alive` 双层 fail-open**：读裸键恒 True（bug 本体）+ `except: return True`（存储不可查 fail-open，注释称 bar 过期门兜底）→ 66b 修第一层；第二层保留（buy_ok 的 bar 新鲜门兜底，语义成立）。
7. **流消息 payload 只在 crypto 分支带 account_id**（md_hub/main.py:233-234 `msg_of`）——键统一而 payload 不统一=A 股 bar 将被删跳过分支后的 `_account_mismatch` 全拒（双盲同判 P0，§2.2 契约化）。
8. **`quant-live-task@.service:5-6` 硬依赖 `After=/Wants=quant-md-hub@quant.service`**（A-P1-3）——66b mask @quant 后任务启动依赖失败噪声+worker↔hub 启停次序失守 → 66b 文件清单收编（去具体实例依赖，SA4 周期调和已兜覆盖）。
9. **D26 §3.4③ 半根桶规则字面不可用**（A-P1-5）：「span<60s→untrusted」恒真（正常活跃分钟桶 span≈55-59s∈[0,60)）→ 按字面=每根 bar 冻结=全市场停 BUY；正确基底=现行 `_finalize`（parts.py:176）双门限 → 66c 按双门限实现，**D26 §3.4 同步改写**。
10. **rewarm 不推进 `state.max_ts`**（仅 handle_msg 接受 bar 时推进）——D26 §3.4②「重启后 rewarm 推进水位过缺口即解冻」按现行代码落空（盘中重启后仍冻结到次日换段）→ 66c 补「rewarm 后显式推进 max_ts」（D26 同步改写）。

---

## 1. 终态架构（三批共同目标）

### 1.1 拓扑与进程模型

```
external_interface（trading 域 enabled 行，全市场）
  └─ 每行 ⇒ systemd unit quant-md-hub@{row.id}（数字实例名=row_id）
        ├─ ACCOUNT_ID=%i（数字）→ 全键自动 per-account + 流消息 payload 恒带 account_id
        ├─ 常驻（行存在⇔hub 在场；A 股窗外挂起态：logout 保持 CREATED 不产 bar）
        ├─ 连接生命周期=hub 内部状态机（renew/suspend 原语，禁进程重启管理时段）
        └─ boot 守卫 SET NX EX 30 + 续租 5s EXPIRE 30 + gen INCR（a′，无仲裁）
worker（每任务子进程，TD-only）
  └─ 读 live_task.account_id → 消费 hub:bars:{account_id}:{symbol}（单一谓词）
```

### 1.2 键模型对照表（旧 → 新；66b 切换）

| 键族 | 现状 | 终态 | 生产者 | 消费者 |
|---|---|---|---|---|
| bar 流 | A股裸 `hub:bars:{symbol}`／加密 `hub:bars:{acct}:{sym}`（hub 分支=market 字段，worker 分支=symbol 后缀×account 双条件） | **`hub:bars:{account_id}:{symbol}` 全市场唯一形态 + payload 恒带 account_id 字段** | md_hub/main.py:213-215（键）+233-234（payload） | hub_worker.py:37-41、databus.py:194 |
| hub 心跳 | A股裸 `quant:hb:md-hub`／加密 `quant:hb:md-hub:{acct}` | **`quant:hb:md-hub:{account_id}`** | md_hub/main.py:402（经 `_key`） | hub_worker.py:116、collector.py:224、monitor.py、system.py:114、quant-hbcheck、run_hub_postverify.py |
| worker 水位 | A股 `hub:worker:max_ts:{symbol}`／加密带 acct | **`hub:worker:max_ts:{account_id}:{symbol}`** | hub_worker.py:172/256 | 同（重启恢复 173-180） |
| latest_tick | 写已 account 化；读者裸 GET | 键不变；**读者改 SCAN `hub:latest_tick:*:{symbol}` 取 payload ts 最大**（TTL 65s 封在场+ts 封陈旧） | parts.py:317 | stock_detail.py:168 |
| M5 键（intent/active_instance/guarded） | 在产 | **退役删除**（含 `_lease_acquire` boot 时对 `hub:active_instance[:{id}]` 的无 TTL SET——parts.py:215-219，66a 一并删） | — | — |
| 旧裸键残留 | `hub:bars:{symbol}`/`hub:worker:max_ts:{symbol}` 无 TTL | **回灌后清理**（66b 波次内：旧流 XREAD→新流 XADD 补 account_id 字段，再删旧键） | — | — |

### 1.3 模块边界与功能职责（终态矩阵）

| 模块 | 职责（本批后） | 不做（边界） |
|---|---|---|
| `md_hub/main.py`+`parts.py` | 读接口行（row_id **必填**）开 MD；写流（键+payload 双 account 化）/心跳/latest_tick；boot 守卫+续租+gen；iface 轮询扩凭证摘要；常驻状态机 | 不碰 TD；不做 intent/active_instance 仲裁（M5 退役）；不做时段驱动的进程启停 |
| `strategy_runner/hub_worker.py` | 流消费+同源校验（**无跳过分支**）+水位键+buy_ok 门+缺口检测（66c） | 不判定 provider/market（symbol 后缀判定退役，只认 account_id） |
| `strategy_runner/main.py` | --task-id 唯一路径（**旧 --id 路径退役**）；ctx.account_id=DB live_task 行 | 不做 min(id) 默认行兜底 |
| `data_platform/databus.py` | `subscribe(symbols, *, account_id)` 键化（A03 M5 第二流消费者） | 不做多标的（维持现状） |
| `data_platform/stock_detail.py` | latest_tick SCAN 读者（多账号取 ts 最大） | 不读裸键 |
| `strategy_framework/broker.py` | `get_interface_row(row_id: int, ...)` **row_id 必填 raise** | 不做缺省选行 |
| `scheduler/tasks.py` SA4 | 期望源=**trading 域 enabled 全市场行**生成 `quant-md-hub@{id}`；去 crypto 过滤；builtin @quant 条目退役；**上产前 diff 预演**（prod/staging 预跑新 `_desired_units` 人工确认将拉起的 unit 集——防 enabled EMQ 行意外首启，A-P1-6） | 不无条件 append 常开条目 |
| `health_monitor/` | N 实例快照（collector 含 `render_prometheus` 的 `quant_hub_*` 8 指标加 account_id 维度）+per-account streak（monitor R4/R6）+R4 交叉发现（**源=systemctl list-units 在场集 × SCAN hb 键**，非 DB——保「unit active 但 hb 键消失」唯一探测器，B-P2） | 不做期望集比对（归 SA4 周期对账+hbcheck 发布探针） |
| `web_api/routes/system.py` | `/api/_probe` hub_hb=**在场集健康+空集地板**；SCAN pattern 钉死 `quant:hb:md-hub:*`（尾段冒号隔离垂死裸键，A-P1-1②） | 期望集比对（职责分离） |
| `deploy/` | hub 波次 units **DB 驱动**（preflight 经 quant-dbro 扩 hub 子命令，SQL 返全行含 provider 列、**provider 白名单过滤在 playbook 侧**与注册表同步义务入文档，B-P1-5）；quant-hbcheck **DB 驱动期望集+两代自适应**（§2.5）；旧 @quant stop+disable+mask（**特权通道前置**，§3-7） | SCAN 不做门（至多附加校验） |
| `web/` 前端 | Health 页 hub N 实例卡片；Interfaces trading 域行撤拖拽手柄 | 不动 position 列/reorder 端点（保留） |
| `quant_common/markets.py`（66c） | MarketSpec 纯数据扩展（trading_day_anchor/sessions_skeleton/channels/flush_policy）+三处归属收编 | 不含行为逻辑 |

### 1.4 退出码矩阵（66a 终态）

| 码 | 语义 | 生产者（终态） | unit Restart |
|---|---|---|---|
| 0 | 优雅退出 | main.py:433（finally）/让位路径随 M5 退役 | Prevent |
| 1 | 故障重拉（含 **a′ 续租 CAS 败**，原 5 改此） | main.py:106/109（guarded 退役后清）/161/415 + 续租败 | 重拉 |
| 3 | boot 守卫失败自愈（真让位） | parts.py:234 | 重拉（不进 Prevent，2026-09-22 staging 实证） |
| 4 | Valkey 不可达重试耗尽 | parts.py:236 | 重拉 |
| 9 | 接口行 provider **或凭证**变更（66a 扩比对） | main.py:265 | **自动重拉（RestartSec=30，维持现状不进 Prevent）**——30s 窗=D26 §4.2 承诺载体（B-P1-3 修正 v1 错写） |
| 78 | EX_CONFIG | main.py:254 | Prevent（SA4 78 黑洞去重告警） |
| 5/6 | **退役**（M5 唯一生产者） | 删除 | Prevent 改 `0 78` |

---

## 2. 接口契约（新增/变更签名全集）

### 2.1 键与判定（quant_common 或就地）

```python
# 终态统一：account_id 单一谓词，symbol 后缀判定退役
bar_stream_key(symbol: str, account_id: int) -> str      # 返回 "hub:bars:{account_id}:{symbol}"；account_id None/非法 → raise ValueError
hub_hb_key(account_id: int) -> str                        # "quant:hb:md-hub:{account_id}"（新助手；hub 侧复用 parts._key）
worker_max_ts_key(symbol: str, account_id: int) -> str    # "hub:worker:max_ts:{account_id}:{symbol}"
```

### 2.2 hub 侧（md_hub）——含 payload 契约（双盲同判 P0 落地）

```python
# main.py 启动（终态）：实例名即 row_id；解析优先序钉死（A-P0-2/B-P1-2）：
#   row_id = int(interface_row) if interface_row else account_id     # HUB_INTERFACE_ROW 在场优先（仅 concrete unit 注入，禁入共享模板）
#   两源皆空 → EX_CONFIG(78)；ACCOUNT_ID 非数字且无 interface_row → EX_CONFIG(78)
# ⚠ 禁止将 HUB_INTERFACE_ROW 写进共享模板 quant-md-hub@.service——加密 hub @4/@5 会读 A 股行→双写裸流+seq 交错→gap 风暴→实盘 sticky 冻结（A-P0-2 事故链）
#   过渡注入=concrete unit 整文件遮蔽（编码裁定：drop-in .conf 过不了 install-units *.service 文件名校验）：
#   仓内 server/scripts/systemd/quant-md-hub@quant.service（遮蔽模板 @quant 实例化，不影响 @4/@5），
#   Environment=HUB_INTERFACE_ROW=__HUB_ROW_ID__ 占位符 → release.yml 阶段 2 三新 task（stat/assert 哨兵拦/replace）
#   按 inventory.hub_quant_row_id 注入（staging/prod 行 id 不同不可写死；replace 在指纹采集前=值变化触发单元重装）；
#   66b unit 换名后 concrete 文件+replace task+inventory 键整体退役
get_interface_row(row_id: int, md_only: bool = False) -> dict   # broker.py:214——row_id 必填，None → raise ValueError("row_id required")
msg_of(...) -> dict                                        # 【payload 契约】account_id 字段全市场无条件写入（不再只 crypto 分支）——键与 payload 双统一，缺此=A股 bar 全拒
_poll_iface_switch(r, prev_version, row_id, current_provider, prev_cred_hash) -> (version, cred_hash, switched)
#   扩比对：provider 变更 ∨ credentials 摘要变更（sha256(credentials_encrypted 解密后明文串)，编码时核对解密路径可行性，不可行则 updated_at 兜底并注记）→ os._exit(9)
```

### 2.3 worker 侧（strategy_runner）

```python
_hub_alive(r, account_id: int) -> bool     # 读 per-account 键 exists；except 保留 fail-open（bar 新鲜门兜底）
_account_mismatch(fields: dict, symbol: str, account_id: int) -> bool   # 无 A 股跳过分支：account_id 必有值，int(fields["account_id"]) != account_id → True（payload 契约保证字段在场）
# ctx.account_id 必填（--task-id 路径从 live_task.account_id 来；旧 --id 路径退役后无 None 态）
```

### 2.4 databus / stock_detail

```python
databus.subscribe(symbols: list[str], *, account_id: int) -> StreamHandle   # 键化；调用方传 routing 解析的活跃 trading 行 id（编码步定位调用点逐点接线）
# stock_detail._quote_block：SCAN MATCH "hub:latest_tick:*:{vt}"（COUNT 500 显式——SCAN 成本∝键总量非命中量，web 线程 socket_timeout 内完成，B-P2）→ 解析 account_id → 取 payload ts 最大者；fallback=腾讯 get_quote 不变
```

### 2.5 SA4 / deploy / 观测面

```python
# SA4 _desired_units（终态）：
#   hub units = [f"quant-md-hub@{row.id}" for trading 域 enabled 且 provider ∈ 已注册 MD 网关]   # 全市场，去 crypto 过滤；provider 白名单保留（防 78 拉起黑洞）
#   builtin SA4_HUB_UNIT 常开条目删除；vid.isdigit() 分支保留（数字=账号 hub）
#   拉起熔断 source 标签条件（tasks.py:1537 "builtin"/"hub:crypto"）同步改为新期望源标签——维护标记防反拉语义不得静默失效（A-P2）
# deploy preflight：hub units = quant-dbro 扩 hub 子命令（SQL 返 trading 域 enabled 全行含 provider 列）→ playbook 侧 provider∈MD 注册表过滤（白名单真源=python list_md_gateway_providers；**同步义务钉在过滤处代码注释**——注释指向注册表真源，模块契约清单不含 deploy 故以代码注释为载体）→ systemd 采集降级为附加校验；fallback ['quant-md-hub@quant'] 删除
# quant-hbcheck（重写，两代自适应，B-P0-2）：
#   读 DB 期望集（逐 trading 域 enabled 行）→ 逐行验 hb 键 TTL>0 + 8 字段齐 + hash 内 ts 判龄（≤90s，防旧心跳 90s 残留假过）+ 期望集非空断言
#   空集豁免：期望集为空（bootstrap 零行/维护期全禁用）→ 显式输出「期望集空」跳过而非静默绿
#   两代自适应：per-account 期望行全缺且裸键在场 → 回落裸键校验+输出「legacy 模式」（解 wrapper 装位件不随 release 版本化的时序悖论）；SCAN 至多附加不做门
# /api/_probe hub_hb：SCAN "quant:hb:md-hub:*"（pattern 钉死，尾冒号隔离裸键；COUNT 100 显式——hb 键量级=N 账号个位数）→ 在场集全部新鲜（TTL>0 且 ts 判龄）且在场集非空 → ok；空集 → not ready（空集地板）
# collector：snap["hubs"] = {account_id: {gen, subs, ticks, bars, sess_ticks, last_tick_ts, dropped_pg, ...}}（N 结构）；render_prometheus quant_hub_* 8 指标加 account_id 维度（B-P2）
# monitor R4/R6：per-account streak 键 quant:hm:hub_lost_streak:{account_id} 等（JSON dict 持久化）；component="md-hub:{account_id}"
# R4 交叉发现=DB 期望集×SCAN 在场集（代码裁定改写原 systemctl 交叉设计：DB 行缺席发现语义更准+
#   野 hub〔unit active 而无 DB 行〕由 SA4 L3 ≤300s 兜停，R4 不重复覆盖；legacy 裸键收编在场=
#   切换/回滚过渡态豁免 R4 防迁移夜假 critical——盲审 B-P2-2）
```

### 2.6 66c 契约（MarketSpec + 缺口检测）

```python
# quant_common/markets.py 扩展（纯数据）：
MarketSpec.trading_day_anchor: Literal["natural", "utc0", "night_open"]
MarketSpec.sessions_skeleton:  日盘/夜盘/24x7（骨架；market_hours 表=运营真源不变）
MarketSpec.channels: {name: {stream_maxlen, 订阅上限}}     # bar/depth_snapshot/trade_tick/order_tick
MarketSpec.flush_policy: 三窗 finalize | stale 周期
# worker 缺口检测：
_ts_gap_frozen(ts_key: int, max_ts: int, sessions: SkeletonView) -> bool   # ts_key − max_ts > 60s 且在 sessions 连续段内（段首沿豁免）；检出 → frozen["sticky"]=True + code="frozen.stream"
# 解冻闭环（A-P1-4）：_rewarm 完成后 state.max_ts 显式推进到回放/PG 最大 ts（跳洞=操作者显式接受；否则现行 rewarm 不推水位→盘中重启仍冻结到次日）
# 聚合器半根（A-P1-5 修 D26 字面恒真规则）：沿用 _finalize 双门限基底（parts.py:176 span+count）收紧——span<30s 且 tick 数<3 → 消息 untrusted=1；worker 既有 untrusted 冻结链自动接管
# order_log：加 bar 指纹列（迁移，JSONB 单列 bar_fingerprint：ts/OHLCV/gen 随下单落）
```

---

## 3. 约束条件（全批强制）

1. **顺序约束（D26 §五立法）**：#6（删缺省分支+退役旧 --id 路径）必须在键切换波次（deploy 阶段 7）**前**完成 → 66a 先行上产；#5/#7/#9/#13 与 hub 键切换**同波次**（同一版本一次上产，管道阶段隐式保证）→ 66b 不可再拆上产。
2. **全市场一批**：A 股/加密不拆（消费面统一本身动加密路径）；staging 无加密账号行——crypto 路径验证义务=单测+加密开闸前必修清单（#2 `_hub_alive` 已列）。
3. **部署窗三段**：交易日 8:55/11:35-12:40/15:05；66b 上产=非交易日窗或盘后段（实际按完成时点落窗）。
4. **回滚=整版本 rollback+带外 SOP**（B-P1-1/A-P1-2 合并裁定）：rollback-tasks.yml 补三块——①hub 清单 DB 重采（独立回滚时变量不断裂）；②@quant 复活块（unmask+enable）；③新 unit 停用块（stop+disable @{id}）。**SOP 顺序钉死：先 stop+disable @{id}，再 unmask+enable @quant**（反序则旧 SA4 自动 stop 数字名新 unit 与 unmask 的 @quant 并存→双写裸流窗→worker gap→回滚当场把实盘任务打成 sticky 冻结）。inventory `deploy_hub_units` 缺省删除后 rollback 侧变量兜底同批处理。
5. **不做的事**：不动 TD 侧（批 65 已立）；不动 position/reorder（§4.3 裁定保留）；不动 market_hours 运营真源；不做双源对账（归 M7）；A03 完整五层总线不在本批。
6. **文档同步**：模块契约 md_hub/strategy_runner/data_platform/**health_monitor/scheduler**（B-P2 补两份）+ 接口契约 §流协议 + 手册 05-数据源切换.md（switch.py 退役——66a 加「已退役」占位注记、66c 重写）+ D26 状态注记——随批 66c 收尾一次过。
7. **特权通道前置（B-P0-3+B-P0-2 修后终版，66b 硬前置四件）**：仓内工件已备（quant-svc 扩 disable/enable/unmask——**mask 弃用**：/etc concrete 残件占位下 systemctl mask 必败〔实测〕且 mask 断回滚复活承载件，防复活由新代码 ACCOUNT_ID 断言 78 Prevent 兜底〔A/B P0 同判裁定〕），装位=michael 一次性（重跑 bootstrap 或定向 copy 到 /usr/local/sbin，staging+prod 各一次）：①quant-svc 扩动词版；②quant-hbcheck v2（两代自适应）；③quant-dbro hub 动作版（SQL 扩 trading 域行查询）；④**回灌**（release 完成后立即：`sudo -u quant bash -c 'cd <deploy_root>/server && set -a && source ../shared/.env && set +a && ../shared/venv/bin/python scripts/backfill_hub_bars.py --account-id 1' ` 先干跑再 --commit——gen 置 0 消倒挂，回写 §3-9）。
8. **上产前检查**（66b）：①prod/staging 预跑新 `_desired_units` diff 人工确认将拉起 unit 集（防 enabled EMQ 行意外首启，A-P1-6）；②在跑任务无 frozen 态（部署重启会隐式解冻存量 sticky=未经显式接受带洞窗，A-P2-4——有则在窗内先处置）；③66a 退役 --id 前扫尾 `list-unit-files 'quant-strategy@*'` 确认无存量 ad-hoc --id 单元（防重启风暴，A-P2-3）。
9. **暖机代价与回灌（B-P1-4+A-P1 修后终版）**：键切换后新流空+PG bar_1min 空（hub 落库 09-23 已退役）→ 不回灌=唯一任务首个交易日零历史起步（240 根≈4 小时盲窗）。**回灌=带外步④**（release 完成后立即跑——deploy 用户无 quant 侧执行权限〔shared/venv 700〕，playbook task 结构上不可行，§3-7 清单④五要素）；脚本 `server/scripts/backfill_hub_bars.py`（干跑默认/--commit/--delete-old；**消息 gen 置 0**——保留旧 gen 会与新 hub gen〔从 1 起〕倒挂=live bar 永久 stale_gen 拒=静默全盲，A-P1）；周二 09-29 观察专项加「worker 暖机根数/首信号时刻」。
10. **观察窗排期（A-P2-1）**：周一 09-28 已承载批 64/61/65/63P4 四批+66a 顺带——**66b 观察专项挪周二 09-29**（第六改动同交易日归因困难）；周一窗仅追加 66a 观察项。
11. **换名窗双活期注记（B-P2，写明非消除）**：celery 波次重启后新 SA4（≤300s 周期）可能**先于** deploy hub 波次拉起新 unit @{id}——与旧 @quant 并存数分钟（跨名无 fencing、systemd 同名串行论证不适用于两个不同名 unit；release.yml 的 stop 旧 unit 任务并非先于新 unit 启动，只保证波次内终态唯一）。无害性：两进程写**不同键空间**（旧裸键 vs 新 per-account 键），worker 按自身 account_id 只消费其一，无双写冲突；代价=双 XTP MD 连接瞬态（同账号双行情连接，XTP 侧可容忍，观察窗记录连接计数）。

---

## 4. 分批展开

### 66a · 切换前收束批 ✅ **全链交付**（2026-09-26，prod `202609262243-8d87575`，commit `8d87575`+`c1b88ba`；方案四轮审+代码双盲全修+staging 彩排绿〔ok=76〕+prod 全弧实证〔concrete 行 1 生效+hub gen=203+a′ 格式日志〕；步 8=周一窗顺带观察）

- **目标**：M5 退役（a′ 守卫形态）+ #6 缺省选行/旧路径退役 + 凭证变更 exit 9 + 白名单预登记 + 前端撤拖拽。
- **依赖（就绪）**：§0.2 全项；**前置带外检查**=§3-8③（--id 存量单元扫尾）。
- **产出**：
  - `md_hub/switch.py` **删除**；`md_hub/main.py` 拆机关：`_read_intent`(65-82)/`_boot_dispatch`(85-119)/`_intent_poll`(368-378)/续租 intent 前置(354-356)/M5 import(22-36) 全退役；boot 路径统一走 `_lease_boot`（SET NX EX 30，parts.py:223-236 保留）；续租败 `os._exit(5)`→`os._exit(1)`（main.py:362）；guarded Lua（parts.py:239-275）退役、首接 INCR 并入 `_lease_boot` 冷启路径（守卫通过后 INCR，NX 失败不自增）；**`_lease_acquire` 的 `hub:active_instance[:{id}]` 无 TTL SET 段（parts.py:215-219）一并删**（B-P2——不删则键清理后每次 boot 写回）
  - unit `quant-md-hub@.service`：Prevent `0 6 78`→`0 78`（:32）+注释矩阵更新（:21-30，9=自动重拉语义写明）；`run_hub_postverify.py` 旧注释同步
  - `strategy_framework/broker.py:214`：`get_interface_row` row_id 必填 raise；缺省 SQL(234-238) 删
  - `strategy_runner/main.py`：旧 --id 路径退役（288/323-325/367-404——argparse 收敛为 --task-id 唯一；min(id) 兜底删）
  - `md_hub/main.py:242-246`：row_id 解析优先序钉死（§2.2：HUB_INTERFACE_ROW 在场优先→account_id→皆空 78）；**concrete unit 过渡件** `server/scripts/systemd/quant-md-hub@quant.service`（整文件遮蔽模板 @quant 实例化——install-units 只收 *.service，drop-in .conf 过不了校验；`__HUB_ROW_ID__` 占位符+release.yml 阶段 2 三新 task〔stat 存在探测/assert `>0` 哨兵拦/replace 注入〕+两套 inventory `hub_quant_row_id: 0` 哨兵键；**禁入共享模板**——A-P0-2 事故链防；过渡期模板改动须双写同步 concrete〔文件头注释立法〕）
  - `md_hub/main.py:122-141`：`_poll_iface_switch` 扩凭证摘要比对（§2.2）
  - 白名单文件预登记（§4.2 冷切换丢根三件套：现象/容差/验证义务——条目随文件存在，引擎随批 62 生效）
  - 前端 `InterfacesCard.vue`：canDrag 接线 `ChannelTableShell` 行级基建——trading 域行不可拖（行谓词，capFilter 全禁逻辑保留）；pull 域行保留
  - 手册 05-数据源切换.md 加「已退役（随批 66a M5 退役）」占位注记（66c 重写）
  - 测试：`test_lease_m5.py` 改写为 a′ 守卫测试（boot NX/续租 CAS/让位 exit 3/Valkey 死 exit 4/续租败 exit 1）；`test_md_gateway.py:55-83` 缺省分支用例改必填 raise；worker 旧路径测试收敛；凭证变更用例新增
- **限定范围**：md_hub（main/parts/删 switch）+ broker.py + strategy_runner/main.py + unit 模板/concrete 过渡件 + release.yml 三 task + inventory 两键 + InterfacesCard.vue/ChannelTableShell.vue + 白名单文件 + 手册（真相源+镜像）+ 对应测试；**不碰** hub_worker.py 键逻辑（66b）+ deploy 波次语义 + 观测面
- **部署前置（五要素）**：①**inventory `hub_quant_row_id` 人工填值**（机器=staging/prod 各自；账号=deploy/.venv ansible；无密码〔wrapper 通道〕；前置=Web 集成中心-外部接口页查 **A 股 trading 域行 id**（⚠ 必须确认 provider=xtp 且 market=astock——填 EMQ/加密行 id=hub 以错数据源运行非 fail-fast，A-P2-2）；不填=release 阶段 2 assert 拦〔哨兵 0〕）②存量扫尾：两环境 `systemctl list-unit-files 'quant-strategy@*'` 确认无 --id 存量单元（§3-8③）
- **验收标准**：`pytest server/tests/ -q` 全绿（基线不回归+新增：a′ 守卫 10 钉+iface_poll 时序弧+row_id 必填钉）；`python -c "from src.md_hub import main"` import OK；`systemd-analyze verify` unit 模板+concrete（占位符代入）过（本地）；`grep -r "intent\|active_instance" server/src/md_hub/` 零残留；staging 彩排绿（hub 波次重启后 hub 起来、心跳裸键照常——66a 不切键、**concrete 生效实证=hub 读到显式行 id**）
- **mock 方式**：Valkey mock（fakeredis 或 MagicMock eval/exists/set）；接口行 fake dict（provider/credentials/updated_at）；凭证摘要 sha256 fake 串
- **参考文档**：D26 §4.1/§4.2 + 模块契约 `docs/architecture/模块契约/md_hub.md`

### 66b · 键切换波次批（一次上产，管道阶段保证顺序）

- **目标**：hub unit 换名+四键族 per-account+payload 契约+消费面键化+SA4 期望源+deploy DB 驱动+观测面 N 化+旧流回灌+旧键清理——同版本交付。
- **依赖（就绪）**：66a ✅（#6 完成是波次前置）；**特权通道前置**=§3-7（quant-svc 扩动词+quant-hbcheck 两代版装位——michael 带外步，staging+prod 各一次）；**上产前检查**=§3-8①②。
- **产出**（按管道阶段序）：
  1. **hub 侧**：A 股 hub unit `quant-md-hub@quant`→`quant-md-hub@{row.id}`（实例名=row_id；**concrete 过渡件退役三件套**：删仓内 concrete 文件〔release 三 task 经 stat 自动静默退役〕+inventory `hub_quant_row_id` 键清理+服务器 /etc concrete 残件 stop/disable/mask 随 §4-66b-5 mask 条目同批）；`md_hub/main.py:213-215` bar 流写键分支删（统一 `_key(BAR_STREAM_PREFIX, account_id)+":"+symbol`）；**`msg_of`(233-234) account_id 字段全市场无条件写**（payload 契约，双盲同判 P0）；in_session 谓词（main.py:424-427）暂保持（66c 收编）
  2. **worker 侧**（hub_worker.py）：`bar_stream_key` 单一谓词（§2.1，`_crypto_provider` 判定退役）；`_account_mismatch` 删 A 股跳过分支(51-52)；`_hub_alive(r, account_id)` per-account（**修加密 fail-open**）；水位键统一(172/256)；rewarm(143/182-197) 随 stream 变量自动跟随；`run_worker_smoke.py:28` 冒烟键同步
  3. **databus/stock_detail**：`subscribe` 键化+调用点接线（§2.4）；`_quote_block` SCAN 读者
  4. **SA4**（scheduler/tasks.py）：`_desired_units` 期望源改造（§2.5——builtin @quant(1280/1324/1348) 删、crypto 过滤(1342-1345) 去除改全市场、provider 白名单保留、**source 标签条件(1537) 同步**）；`_sa4_hub_guards` lease 键语义注记清理
  5. **deploy**：release.yml preflight hub units 改 quant-dbro hub 子命令 DB 驱动（§2.5；systemd 采集降附加；fallback @quant 删；三套 group_vars `deploy_hub_units` 缺省与 `deploy_hub_hb_redis_key/url` 死配置清理）；`quant-hbcheck` 两代自适应重写（§2.5——装位随 §3-7 带外步）；release.yml 阶段 7.6 旧 unit 退役（stop+disable，**mask 弃用**——concrete 残件占位必败+断回滚承载件，A/B P0 裁定；残件保留=回滚复活承载，新代码 78 Prevent 兜底防复活）；**回滚剧本补块**（§3-4：hub DB 重采+@quant 复活+@{id} 停用+SOP 顺序）；`run_hub_postverify.py` 键改 per-account；`quant-dbro` 扩 hub 子命令（SQL 返全行含 provider）
  6. **观测面**：collector.py N 化（CORE_UNITS :15-21 去 @quant 硬编码+snap["hubs"] N 结构+SCAN+**render_prometheus quant_hub_* 指标 account_id 维度**）；monitor.py R4/R6 per-account streak+component 去重键+R4 交叉发现（源=systemctl 在场集，§2.5）；system.py `/api/_probe` hub_hb 在场集+空集地板（pattern 钉死）；前端 `ConnectionCards.vue` N 实例卡片（v-for）
  7. **systemd 侧**：`quant-live-task@.service` 去 `After=/Wants=quant-md-hub@quant.service` 具体实例依赖（:5-6，A-P1-3）+全量 grep systemd 目录其余 @quant 引用清零
  8. **波次内回灌+清理**（hub unit 换名后）：旧流回灌 task（§3-9）→ 旧裸键清理（`hub:bars:{symbol}` A股全量+水位键+active_instance/intent 残留键——脚本化键清单）
- **限定范围**：上表 8 组文件+测试；**不碰** TD 侧/交易门面/前端 Interfaces（66a 已收）
- **验收标准**：`pytest server/tests/ -q` 全绿；**键形断言反转**：`test_d3_stream_protocol.py:22`（A 股忽略 account_id 旧断言→必填断言）+`grep -rn "hub:bars:\"\|quant:hb:md-hub\"" server/tests/` 旧裸键形态清零（test_databus:160/249、test_xsleeper:39/71/77/126、test_stock_detail:100、test_hub_worker_migration:21 逐个更新，B-P2）；**payload 行为级断言**：端到端 bar 冒烟（假 tick 走 hub→worker 或 run_worker_smoke 进彩排清单）——worker stats.bars 递增且 dropped_cross==0（A/B 同判 P0 验收项）；staging 彩排绿含：新 unit @{id} 拉起+心跳 per-account 键+`/api/_probe` ok+hbcheck 期望集+**旧 @quant unit masked**+回灌后新流非空+hub 波次零失败+回滚演练（@quant 复活块实测）；观察=周二 09-29 专项（§3-10：四键族/R4R6/波次/暖机根数/首信号时刻/subs≥running 任务标的数）
- **mock 方式**：fakeredis SCAN/TTL/xrevrange/xread/xadd；DB 期望集 fake 行集（trading 域 enabled/disabled+provider 未注册态）；unit 清单 fake；deploy 检查=本地 pytest 模拟清单运算；回灌=旧流 fake 条目→断言新键条目数与 payload 字段
- **参考文档**：D26 §3.3/§4.3/§五 + 模块契约 md_hub/strategy_runner/data_platform + deploy-mechanism 记忆

### 66c · 稳定性配套批（独立上产）

- **目标**：§3.4 四件（ts 缺口检测/解冻闭环/半根桶 untrusted/bar 指纹）+MarketSpec 纯数据+三处归属收编+worker 时段门修（§0.4-1）+文档收尾。
- **依赖（就绪）**：66b ✅（键模型终态后做缺口检测，避免二次改键域）；批 62 未实施不阻塞（白名单已预登记，验收不依赖）。
- **产出**：
  - `quant_common/markets.py`：MarketSpec 扩展（§2.6 纯数据）+三处重复归属收编（交易日锚/时段骨架/flush——编码时按 v2 立法定位三处现行实现收编入 markets.py）
  - `hub_worker.py`：`_ts_gap_frozen` 时段感知缺口检测（连续段内+段首豁免）+检出 sticky 冻结；**rewarm 后 `state.max_ts` 显式推进**（解冻闭环，§2.6——单测：盘中冻结→重启→rewarm 推水位→不再触发）；`_in_astock_session()` 前置改 MarketSpec per-market sessions（修加密 24/7 误伤：:202 buy_ok in_session 与 :238 STALE_PUB_S）
  - md_hub 聚合器：半根桶 **双门限**（span<30s 且 tick<3 → untrusted=1，沿用 `_finalize` parts.py:176 基底——D26 §3.4③ 字面规则已同步改写，见 §6 处置）
  - 迁移：order_log 加 bar 指纹列（JSONB 单列 `bar_fingerprint`：ts/OHLCV/gen 随下单落）
  - 文档同步收尾（§3-6：模块契约×5——md_hub/strategy_runner/data_platform/**health_monitor/scheduler**+接口契约+手册 05 重写+D26 状态注记「已实施」）
- **限定范围**：markets.py+hub_worker.py+md_hub 聚合处+迁移 1 份+order_log 写点+文档；**不碰** deploy/SA4/前端
- **验收标准**：`pytest server/tests/ -q` 全绿；缺口检测用例（午休不触发/隔夜不触发/盘中 60s+ 触发/段首豁免/**重启解冻闭环**）+半根桶双门限用例（正常活跃桶 span 55s 不标/半根 span 20s tick 2 标）+加密时段门用例（BINANCE symbol 盘外 buy_ok=True）；alembic upgrade/downgrade/幂等三验
- **mock 方式**：SkeletonView fake（日盘骨架+24x7 骨架）；水位键 fake 值；聚合桶 fake span/count
- **参考文档**：D26 §3.4/§3.2 + A03 §8.4 + 模块契约 strategy_runner

---

## 5. 验收总表（三批合一）

| 检查项 | 66a | 66b | 66c |
|---|---|---|---|
| pytest 全绿（基线 1454+ 不回归） | ✅ | ✅ | ✅ |
| staging 彩排绿 | ✅（hub 波次照常+concrete 生效） | ✅（unit 换名+新键+payload 冒烟+hbcheck+masked @quant+回滚演练） | ✅ |
| prod 上产三证+hub 心跳+postverify | ✅ | ✅（+新 unit/gen 跳变/worker rewarm+回灌非空+dropped_cross==0） | ✅ |
| 观察窗（交易日） | 周一顺带 | **周二 09-29 专项**（§3-10） | 下一个交易日 |
| 加密路径（staging 不可达） | 单测承担 | 单测承担（#2 修复列加密开闸前必修清单） | 单测承担 |
| 带外前置步 | §3-8③ 扫尾 | §3-7 特权通道+§3-8①②预演检查 | — |

---

## 6. 方案双盲审处置记录（v1→v2，2026-09-26）

**A 审（金融风控/交易可靠性）：P0×2+P1×6+P2×4；B 审（架构/systemd/Ansible 管道）：P0×3+P1×5+P2×7。两审均确认 §0.3/§0.4 锚点核实属实、D26 §五 13 项无漏项、批内顺序约束满足。两审独立同判 1 条（msg_of payload，P0 最高置信）。**

| # | 级别/来源 | 缺陷 | 处置（落点） |
|---|---|---|---|
| 1 | P0·A+B 同判 | msg_of payload account_id 只 crypto 分支写——键统一 payload 不统一=A股 bar 全拒+静默全盲（_blind_watch sess_bar_wall=0 恒不报警+hub 侧计数双绿掩盖） | §0.4-7/§2.2 payload 契约+66b-1+验收「stats.bars 递增且 dropped_cross==0」+端到端冒烟入彩排 |
| 2 | P0·B | quant-hbcheck 是 root 装位件不随 release 版本化——新旧协议互斥锁死发布链 | §2.5 两代自适应（per-account 全缺+裸键在场→legacy 回落）+§3-7 随带外步装位 |
| 3 | P0·B | quant-svc 动词白名单无 disable/mask/unmask/enable——66b 波次无特权通道 | §3-7 一次性带外步（扩动词+sudoers+重装，michael 执行，staging+prod 各一次） |
| 4 | P0·A（并入 P1·B 同区域） | HUB_INTERFACE_ROW 落共享模板→加密 @4/@5 被 A 股行覆盖→双写裸流+seq 交错→gap 风暴→实盘 sticky 冻结 | §2.2 concrete unit 遮蔽钉死（drop-in 过不了 install-units 校验——编码裁定回写）+优先序+**禁入共享模板**立法 |
| 5 | P1·B | exit 9 移入 Prevent 与 30s 窗承诺矛盾（v1 错写） | §1.4 修正：9 维持自动重拉不进 Prevent |
| 6 | P1·A+B 同区域 | rollback 三断点：无 hub 重采/无复活块/清缺省后变量断裂+反序双杀窗 | §3-4 回滚三块+SOP 顺序钉死+66b 验收含回滚演练 |
| 7 | P1·A | quant-live-task@.service 硬依赖 @quant——mask 后依赖失败噪声+启停次序失守 | §0.4-8+66b-7 去具体实例依赖+grep 清零 |
| 8 | P1·A | 66c 重启解冻按现行 rewarm 不可实现（不推 max_ts→冻结到次日） | §2.6 解冻闭环（rewarm 显式推进 max_ts）+单测；D26 §3.4② 注记同步改写 |
| 9 | P1·A | 半根桶 span<60s 字面恒真（正常桶 55-59s）→全市场停 BUY——**D26 文档 bug** | §2.6 双门限（span<30+tick<3，_finalize 基底）；**D26 §3.4③ 本批同步改写** |
| 10 | P1·A | SA4 去 crypto 过滤后 prod enabled EMQ 行会意外首启未测 hub | §2.5/§3-8① 上产前 diff 预演人工确认 |
| 11 | P1·B | 键切换=worker 暖机全失（新流空+PG 空→周一 4h 盲窗） | §3-9 旧流回灌（波次内 task）+观察项暖机根数/首信号时刻 |
| 12 | P1·B | preflight DB 驱动需 quant-dbro 扩子命令+provider 白名单失守 | §2.5/66b-5 dbro 返全行+playbook 侧过滤+同步义务入契约 |
| 13 | P2·A | 周一窗第六改动归因困难 | §3-10 66b 专项挪周二 09-29 |
| 14 | P2·A | SA4 熔断 source 标签不同步→维护标记防反拉失效 | §2.5 source 标签条件同步+66b-4 |
| 15 | P2·A | #11 订阅断言缺；--id 存量单元扫尾 | §3-8③+66b 观察项 subs≥任务标的数 |
| 16 | P2·A | 重启隐式解冻存量 sticky 任务 | §3-8② 上产前检查 frozen 态 |
| 17 | P2·B | render_prometheus 消费 snap["hub"] 漏改→/metrics 500 | §2.5/66b-6 指标 account_id 维度 |
| 18 | P2·B | R4 交叉发现源未钉死（DB 源丢唯一探测器） | §2.5 钉死=systemctl 在场集 |
| 19 | P2·B | _lease_acquire 仍写 active_instance SET | §1.2/66a 产出（parts.py:215-219 一并删） |
| 20 | P2·B | 文档清单漏 health_monitor/scheduler 契约；手册 05 中间态悬空 | §3-6 补两份+66a 占位注记 |
| 21 | P2·B | 换名窗双活期未写明（新 SA4 先拉新 unit 与旧 @quant 并存数分钟，跨名无 fencing） | §3-10 注记：窗口存在、数据面无害（不同键空间）、双 XTP MD 连接瞬态；stop 旧 unit 并非先于新 unit 启动 |
| 22 | P2·B | 测试锚只点名 test_d3:22 | 66b 验收 grep 旧形态清零断言+全部锚点列名 |
| 23 | P2·B | SCAN 成本∝键总量，COUNT 未定 | §2.4/§2.5 COUNT 500 显式+socket_timeout 注记 |
| 24 | A-P1-1②（量级 P2） | _probe SCAN pattern 会吞垂死裸键 | §2.5 pattern 钉死 `quant:hb:md-hub:*` 尾冒号隔离 |

**D26 文档同步改写两处（真相唯一铁律，随本批 v2 一并落）**：§3.4③ 半根桶 span<60s→双门限；§3.4② 解冻注记→「rewarm 后显式推进 max_ts」（66c 契约）。

**代码双盲审处置（66a，2026-09-26）**：A（交易可靠性）P1×1+P2×4 / B（系统管道）P1×1+P2×4，全修：
- A-P1 **凭证基线首发失败永久悬空**（版本固化短路死锁基线=首次真实轮换漏检）→ 读行失败版本顺延不固化（首发失败返 None 重走首发；版本变化轮失败返 prev 下轮重读）——**编码中自查发现并同修同构洞**（版本变化轮读失败返回新版本+旧摘要=该轮即轮换时同样漏检）；测试补全弧钉（首发失败→重建→轮换触发 / DB 瞬断不吞轮换）。
- B-P1 **撤 trading 拖拽激活壳层潜伏 bug**：ChannelTableShell reorder 载荷=可拖子集 vs 全量校验端点 400（canDrag 66a 首个真消费者激活）→ 载荷改全量 `arr.map(r.id)`（不可拖行原位不动由 splice 保证；其他消费者 canDrag=null 行为不变零回归）。
- A-P2：NX kwargs 钉死（`assert_called_once_with("hub:lease", ANY, nx=True, ex=30)`——丢 nx=双活 fencing 静默失效）；hub_quant_row_id 填错行风险 → 部署前置五要素写明「确认 provider=xtp 且 market=astock」（§4-66a）；文档残留三处（strategy_runner/__init__ docstring 旧启动法/md_gateway 注记/带边界注记「只改凭证不 bump 不触发」）；concrete 双写义务注释。
- B-P2：inventory 注释错挂归位（hub_quant_row_id 行尾误挂 deploy_hub_units 注释）；手册真相源 docs/manual/05 补同款注记（消除镜像分叉）；**任务文件 concrete 机制回写**（drop-in 方案经编码证实过不了 install-units 校验→concrete 整文件遮蔽，7 处改写+66b 清除义务钉三件套）；测试钉缺口如实声明——①沙箱 concrete 道具不加（三 task 沙箱静默跳过已验证，**staging 彩排承担首验**）②row_id 皆空 78 与 ③续租败 exit 1 均为 main() 闭包分支（get_interface_row(None) 等价防御钉已立；退出码矩阵注释+彩排承担）④前端无组件测试基建（B-P1 载荷全量化=结构性修复兜底）。
- 两审核实确认：M5 竞态三时序推演安全（同名重启窗/带外误启/Valkey 半死）/row_id 链无静默错行/退出码生产者与注释逐条对上/--id 零现实消费方/systemd-analyze verify+ansible syntax-check 双过/--check/GC/回滚/双向兼容（旧 exit 5 在新 Prevent 下照常重拉）全推演成立。

**代码双盲审处置（66b，2026-09-27）**：A（交易可靠性）P0×1+P1×1+P2×5 / B（系统管道）P0×2+P1×3+P2×7+P3，全处置：
- **mask 死结（A+B 同判 P0）**：/etc concrete 残件占据 mask 落点必败〔两审本机实测 rc=1〕→ 发布链锁死；且 mask 断回滚复活承载件。**裁定采纳 A 修法**：删 mask task 收敛 stop+disable——残件保留（回滚承载）+新代码 ACCOUNT_ID 断言 78 Prevent 兜底防复活（B 的 rm 残件修法被 A 驳：断回滚）。
- **quant-svc 工件缺失（B-P0-2）**：§3-7 说「随 bootstrap 重装」但仓内 wrapper 未扩动词=无工件可装+手工改被 bootstrap 覆盖 → 仓内落 disable/enable/unmask 扩展（mask 弃用）。
- **回灌 gen 倒挂（A-P1）**：保留旧 gen≈203 vs 新 hub gen 从 1 起 → live bar 永久 stale_gen 拒=静默全盲（心跳绿无告警 SELL 停摆）→ 消息 gen 置 "0"；时点钉 release 后带外步④（playbook task 不可行=deploy 无 quant 执行权限，§3-9 回写）。
- **回滚三块形态闸（B-P1-3）**：无条件执行会在 66b 后回滚（66c→66b 等）拆掉数字 hub+restart 一个 78-Prevent @quant 判死回滚 → 三块加「目标树含 concrete」闸。
- **hbcheck env 注入删除（B-P1-2）**：sudoers env_reset 剥成 no-op 旋钮+生效即读错库（db4 vs 写侧 db0）必红 → 删 environment 块+清 inventory 两死键，回落 wrapper 内 .env 单一真源。
- **沙箱道具（B-P1-1）**：make_sandbox_root 补 hub.out（缺=沙箱 release 全在 preflight 中止，新管道零场景覆盖）。
- **白名单「超集」（A-P2-1 驳回）**：A 断言注册表仅 {xtp,emt_emq}——B venv 实跑 list_md_gateway_providers()=四 provider 全注册（加密 MD 网关 D6 已装）+prod @4/@5 本就在跑=虚惊，白名单维持四项与注册表一致。
- 其余：非空断言豁免门（server_changed+逃生键+static 源 skipped 语义）/monitor state 初值 dict 化（B-P2-1 崩溃防线）/R4 legacy 过渡豁免（B-P2-2 迁移夜假警）/crypto 测试 SECRET_KEY 隔离（B-P2-7 环境红修）/残留 fixture 清零（xsleeper×3+databus）/新行为钉 9 个（_probe 空集地板+ts 陈旧+非数字尾段/collector legacy 双分支/_hub_expected_ids 直测）/P3 注释四处。
- 两审核实确认：波次实序 task→hub（旧注释纠偏）/双活窗不同键空间安全/payload 三层一致+存量旧消息全路径不可达（XREADGROUP>$/xrevrange/xautoclaim 全核）/Jinja 链 venv 实跑正确/skipped register 语义实测/回灌脚本本体合格（键分类/幂等/maxlen 无剪尾）。
- 验证：**1460 全绿**（+6 新钉+1 crypto 环境修复）；syntax-check 双剧本过（YAML name 含 = 的 k=v 误判两处修）。

**快审（第三轮忠实度复核）**：22/24 忠实（含全部 5 条 P0），余 4 项轻量修补已落——#21 双活窗注记虚指→§3-11 正文补写（窗口存在/不同键空间无害/双 XTP 连接瞬态/stop 时序实况）；#23 打折→§2.5 _probe SCAN 补 COUNT 100；§3-9「周一」措辞残留→周二；#12 同步义务载体悬空→钉在过滤处代码注释（模块契约不含 deploy）。#24 来源标注补正（原 A-P1-1②）。快审附带确认：D26 两处改写正确无残留；D26 §4.1:113「续租丢路径均已死」旧句待 66c 文档收尾一并校（既定范围）。**总判 PASS**。
