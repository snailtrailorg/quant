# 批 76b · 冻结史 UI（挂 UI 尾批）

> 立项 2026-10-01（用户「那你先写方案」）。**前置已全部上产**：批 76 F1 事实表 + F2 分级解冻面
> 三批合并上产 prod `202610012108-9d19a40`（八阶段全绿，DB 首达 `0123`）。
>
> **2026-10-01 22:18 用户裁定**：
> - **裁决点①「马上改」** ⇒ `freeze_event.py:93` 的 `str(datetime)` 改 `isoformat()`（见 §裁决点① 定稿）。
> - **裁决点②「另给两方案」** ⇒ 用户提出 a（冻结事件入日志 + 类型筛选）/ b（监控页 hub 卡片状态改冻结）
>   两案，问**与独立页签相比的优劣**。⇒ **新增 §6 三案对比**（本节是本批决策的核心，先读它）。
>   ⚠️ 三案对比推翻了本方案 §2.3 的「监控页全局页签」假设——**B 案（hub 卡片）在数据来源上是假方案**，
>   详见 §6.2。

## 0. 一句话

F1 建的 `freeze_event` 表已经在 prod 里躺着，写点齐全（三 sticky 冻结点 + 启动补记 restart +
手动解冻闭环），查询端点 `GET /api/live-task/{tid}/freeze-events` 也已上产——**但整个前端零引用**
（`grep -rn "freeze" web/src/` 只命中 `UnfreezeConfirm.vue` 那套解冻确认页，与事实表无关）。
结果：**冻结事件在库里累积，能读的只有 `journalctl` 和一次性的 curl**。
本批把这块事实接到 UI 上——「冻了几次、谁解的、隔了多久」从「不可见」变「一眼可见」。

## 1. 现状勘察（2026-10-01 逐点 file:line 实证）

### 1.1 后端已就绪（本批不动）

| 面 | 位置 | 现状 |
|---|---|---|
| 事实表 | `server/migrations/versions/0123_freeze_event.py` | 12 列 + 四 CHECK（枚举 / 解冻方式 / **闭环一致性**：解冻必记方法 / **jsonb 类型守卫**）+ 2 索引（进行中部分索引 + 时间线 `(task_id, frozen_at)`） |
| 读写单点 | `server/src/data_platform/freeze_event.py:80-95` | `list_events(task_id, limit)` → 11 字段 dict（见 §1.2） |
| 查询端点 | `server/src/web_api/routes/trading.py:231-234` | `GET /api/live-task/{tid}/freeze-events?limit=`，perm=**`read`**，limit 夹在 `[1,200]` |
| 写点 ① | `hub_worker.py::_record_freeze` | 三 sticky 冻结点（ts_gap / seq_gap / untrusted），fail-soft |
| 写点 ② | `hub_worker.py::_close_freeze` | 启动起手 `close_freeze(restart)`（上一进程遗留的 open 事件闭环）+ 自动/手动解冻闭环 |
| 告警码 | `alert_notify/runbook.py:26-30` | `frozen.stream` / `frozen.intercept` / `frozen.auto` / `frozen.manual` 四码已入 runbook |

**结论：本批后端改动 = 0。** 若落码时发现需要动 `list_events` 或端点，说明勘察有误——立即停手回查。

### 1.2 返回字段（`list_events` 原样）

```
id, task_id, account_id, symbol, freeze_type, frozen_at, watermark,
gap_target_ts, unfrozen_at, unfreeze_method, operator
```
- `freeze_type` ∈ `ts_gap | seq_gap | untrusted`（库 CHECK 锁枚举）
- `unfreeze_method` ∈ `restart | auto_reconnect | manual_web | manual_im`（库 CHECK 锁枚举；`null` = 仍冻结）
- `frozen_at` / `unfrozen_at` 是 `str(r[5])` 出来的**字符串**（`'2026-10-08 09:31:02.123456+08'`），
  `null` 表进行中。**§3.3 有格式坑**。
- `operator` 对 `manual_im` 是 `"{web用户名}（IM 发起:{im操作人}）"`（`trading.py:217` 拼的），
  对 `restart`/`auto_reconnect` 恒 `null`，对 `manual_web` 是 web 用户名。
- `watermark` / `gap_target_ts` 是 **epoch 秒字符串**（`hub_worker._epoch_key` 产物），
  不是可读时间。`gap_target_ts` 只在 ts_gap 时有值。
- `account_id` 在 `record_freeze` 时由 worker 传入，**可能为 null**（老任务/未绑账号）。

### 1.3 前端现状

| 项 | 位置 | 说明 |
|---|---|---|
| 任务页 | `web/src/views/LiveTask.vue`（324 行） | 已有 `inject('canPerm')` / `canLive`（批 77）/ `navReadonly`；已有展开行插槽（`:44-62`） |
| 展开行现有内容 | `LiveTask.vue:47-59` | 「自愈时间线」= `task_logs` 过滤渲染（`enrichTasks` 按 `live:{tid}` 拉日志）；**已有按需拉取先例**（`:310`） |
| 列显隐 | `LiveTask.vue:188-202` `taskColDefs` + `ColumnSettings` | 新列默认隐、配置持久化 localStorage |
| i18n | `web/src/locales/index.js` | zh 块 `liveTask:` 在 `:366`，en 块在 `:1792`；`unfreezeConfirm:` 有 `type_ts_gap/seq_gap/untrusted/unknown` **四档现成词条**（`:151-160` zh / `:1578+` en） |
| 时间格式 | `web/src/utils/fmtTime.js` | `fmtTime.full(ts)` / `fmtTime.s(ts)` 两档——`full` 处理 ISO 串，`s` 处理短格式 |
| 表格壳 | `web/src/components/TableShell.vue` | `loading` prop + `fill`/`infinite`/`moreText`；空 data 走 el-table 自带空态 |
| 构建门 | `web/scripts/check-locales.mjs`（prebuild） | **只查重复键 + 行内注释吞键**；zh/en 缺键它**不报**（会静默回落）⇒ §5 有自查步骤 |
| 令牌门 | `scripts/check-tokens.sh`（prebuild） | 内联 px/hex/font-size **只许降不许升** ⇒ §3.2 有硬约束 |

**前端零引用 `freeze`**（除解冻确认页）——这就是本批的全部工作面。

### 1.4 权限判据（批 77 立法，本批必须遵守）

| 动作 | 键 | 角色 |
|---|---|---|
| **看**冻结史 | `read` | 四角色皆可（analyst/viewer 也应能看到——这是事实，不是操作） |
| **做**解冻 | `live_control` | trader ✅ / admin ✅ / analyst ❌ / viewer ❌ |

**判据 = 「读事实」与「写控制」分档**：冻结史是**只读事实**，挂 `read`；解冻是**实盘动作**，挂 `live_control`。
后端端点已经是这个分法（`trading.py:232` = `read`）——前端必须**照抄这个梯度**，不能因为「解冻按钮在
任务页」就顺手把冻结史也塞进 `canLive`。analyst 看不到解冻按钮，但**看得到冻结史**——这是对的。

## 2. 方案主体

### 2.1 落点：任务页展开行第二区块（**主**）

**为什么是展开行而不是新页面 / 新列**：
- 事实表按 `task_id` 索引，「这个任务冻过几次」是**任务维度**的问题——展开行就是任务的行内详情，语义天然对齐；
- 展开行已有「自愈时间线」区块，**它的数据源（task_logs）与冻结史（freeze_event）是两套**：
  时间线是**逐条日志**（含非冻结事件），冻结史是**结构化事实**（含起止时刻、解冻方式、操作者）。
  两者**并列不合并**（§2.2 详述）；
- 折叠 = 默认零成本（不展开就不拉），符合「冻结绝不频繁」的真实数据密度。

**硬约束：惰性拉取**。现有 `enrichTasks()` 在 `load()` 后**对每个任务并发**拉 `task_logs`
（`LiveTask.vue:307-313`）。**冻结史绝不能进 `enrichTasks`**——那会让 N 个任务在页面加载时
各打一次 freeze-events（N+1 查询，且 99.9% 的时间返回空）。必须绑到**展开事件**上，展开才拉。

**实现骨架**（落码时按此形状，细节照 `LiveTask.vue` 既有风格）：

```vue
<el-table-column type="expand">
  <template #default="{ row }">
    <!-- ……既有「自愈时间线」区块保持不动…… -->
    <div style="margin-top: var(--sp-3)">
      <div style="…">{{ t('liveTask.freezeHistory') }}</div>
      <div v-if="row._freezeLoading" …>{{ t('common.loading') }}</div>
      <div v-else-if="!row._freezeEvents?.length" …>{{ t('liveTask.freezeNone') }}</div>
      <el-table v-else :data="row._freezeEvents" size="small" …>
        <!-- 六列见 §3.1 -->
      </el-table>
    </div>
  </template>
</el-table-column>
```

**触发放哪**：`el-table` 的 `@expand-change="onExpandChange"`——展开时若 `row._freezeEvents === undefined`
则拉一次并缓存；折叠不重拉（二次展开用缓存，配「刷新」跟着主表 `load()` 清空缓存）。
`row._freezeEvents` 放**行对象上**（与既有 `row._timeline` 同款，`:311` 就是这么存的），
天然随 `load()` 重建对象而失效——**这个失效恰好是我们要的**（启停/解冻后主表刷新 ⇒ 冻结史跟着更新）。

### 2.2 与既有「自愈时间线」的关系（明确不合并的理由）

| | 自愈时间线（既有） | 冻结史（本批） |
|---|---|---|
| 数据源 | `task_logs`（`GET /log?task_id=live:{tid}`） | `freeze_event`（`GET /live-task/{tid}/freeze-events`） |
| 粒度 | 逐条**日志行**（含启动/退出/告警/告警去重前的重复） | 逐条**事件**（一冻一解 = 一行事实） |
| 时刻 | 只有日志时间 | **frozen_at / unfrozen_at 两端**（可算冻结时长） |
| 操作者 | 无 | `operator` + `unfreeze_method`（审计面） |
| 权限 | `read`（403 → `_timeline = null`，`:312`） | `read` |
| 可靠性 | 日志轮转即丢 | **落库持久**（这就是 F1 立表的意义） |

**不合并**：日志是流，事实是账。把日志过滤出「冻结」再当冻结史用，会同时丢掉**解冻端时刻**
（`frozen.manual` 告警有时刻，但 `restart` 补记闭环**没有任何日志**——`_close_freeze("restart")`
只写库、只在有行时 `logger.info`，而 info 不属于告警面）和**操作者**。**冻结史必须读表。**

### 2.3 监控页全局冻结史（**已由用户 2026-10-01 裁定为 C 案**——见 §6.3/§6.4）

> ⚠️ **本节原为「建议不做」。用户随后提出 A/B 两案要求对比 ⇒ 对比升格为 §6，
> 结论：C 案（本节所述页签）**挂账**，主案仍是 §2.1。本节保留为 C 案的技术档案。**

`Observe.vue` 顶层三页签（status / logs / audit）。加第四个「冻结史」页签展示**全站事件流**
（`list_events(task_id=None)`——`freeze_event.py:81` 已支持，但**没有端点暴露**）：

- **代价**：需新增一个 `GET /api/live-task/freeze-events`（全量）后端端点 ⇒ 破坏 §0「零后端改动」；
  且 `list_events` 全量查询**无分页**（只有 `limit`），事件累积后需补 cursor；
- **收益**：跨任务横向看「今天有几个任务冻了」——但这是**运维视角**，而 prod 现在**零冻结事件**
  （批 76 刚上产，10-08 才开市），需求尚未被真实数据验证；
- **建议**：**挂账**。等任务页区块跑过一轮真实冻结（含一次 `ts_gap` 人工解 + 一次 `seq_gap` 自动解），
  确认字段与文案都站得住，再决定要不要横向视角。**先做一个能被真实数据检验的东西，别先做两个。**

### 2.4 任务列表行内「冻结次数」列（**否决，理由已强化**）

`taskColDefs` 加一列显示 `冻结 N 次`（或 `❄ N`）。**否决理由**：数据来源是 freeze-events 端点，
而列表渲染时**每行都拉一次** = §2.1 明令禁止的 N+1；若改成后端在 `GET /live-task` 里带一个
`freeze_count` 聚合字段，就是**改后端 + 改 SQL**（破坏 §0），且「次数」本身信息量低——
**「最近一次冻在哪、解得干不干净」比「冻过 7 次」有用**。冻结史区块里天然有这个答案。
已有的行内 `❄` 标记（`LiveTask.vue:32` `row.frozen`）保留不动——那是**当前态**，
与本批的**历史态**互补，不是重复。

> **§6.2 对比后补充**：用户提出的 B 案（hub 卡片改冻结）实质是**同一诉求的另一种落法**
> ——「想在列表/卡片上直接看出冻结」。两处否决同源：**它们都是「瞬时灯」，而本批交付的是
> 「持久账」**。瞬时灯的正解不是加更多灯，是**给已有灯接一条通往账的入口**（见裁决点③）。

## 3. 展示细节

### 3.1 列设计（六列，全部来自 §1.2 字段）

| 列 | i18n 键 | 渲染要点 |
|---|---|---|
| 冻结时刻 | `liveTask.frFrozenAt` | `fmtTime.full(frozen_at)`——**已实测直用，见 §3.3** |
| 原因 | `liveTask.frType` | **复用** `unfreezeConfirm.type_ts_gap/seq_gap/untrusted` 现成四档词条，不新建 |
| 解冻时刻 | `liveTask.frUnfrozenAt` | `unfrozen_at` 为 `null` ⇒ 显示 `—` 并整行高亮 |
| 解冻方式 | `liveTask.frMethod` | 四档小 tag：`restart` / `auto_reconnect` / `manual_web` / `manual_im`；`null` ⇒ `—` |
| 操作者 | `liveTask.frOperator` | 见 §3.5（IM 拼接串要**截断 + tooltip**） |
| 冻结时长 | `liveTask.frDuration` | `unfrozen_at - frozen_at`；进行中 ⇒ `t('liveTask.frOngoing')`（**"进行中"红字**） |

**进行中高亮（核心可读性）**：`unfrozen_at === null` 的行 = **当下正在冻结**——用行 class
（`:row-class-name` 返回 `'fr-ongoing'`）配 `--warn` 左色条。**这是整块 UI 的唯一「火警」信号**：
一张表里九成是已完成的历史行（中性灰），进行中那一行自己冒出来。

**`gap_target_ts` / `watermark` / `gap_target_ts` 不进主表**：
`watermark` 是 epoch 秒串，人眼读不出；`gap_target_ts` 只在 ts_gap 有值。把这两个塞进
**行 hover tooltip**（或行内「详情」展开）即可——**主表只放能一眼读懂的六列**。

### 3.2 样式硬约束（`check-tokens.sh` 是 prebuild 门，会拦构建）

- **禁止内联 px / hex / font-size**：用 `var(--sp-*)` / `var(--fs-*)` / 语义色令牌。
  现有展开行区块（`:46-59`）就是内联 `var(--sp-2)` / `var(--fs-foot)` 的写法，照抄这个模式。
- **红绿语义**：本项目红=涨/危险、绿=跌/安全。冻结态用 **`--warn`（警示）**，
  「进行中」用 `--warn` 而非 `--critical`——**冻结是保护性动作（拒 BUY 是设计意图），不是故障**。
  真用红色会让「系统地保护了自己」看起来像「系统坏了」。**这条与 `StatusTag.vue` 的
  「中性灰底+彩点」哲学一致**（`StatusTag.vue` 注释：「红绿只给数据」）。
- 表格嵌在展开行里：用 `size="small"` + `border`，**不要再套 `TableShell`**
  （TableShell 带列宽持久化与 EP store 回放，嵌套场景无意义且会给 localStorage 添无谓键）。

### 3.3 时间格式——**已实测，无需处理**（原判风险已排除）

`list_events` 返回的 `frozen_at` 是 **`str(datetime)`**（`freeze_event.py:93`），形如
`'2026-10-08 09:31:02.123456+08'`。**方案初稿标红此点为「落码前必验」——已验，绿。**
实测（Node 22 直跑 `fmtTime.js` 的 `norm()` 原函数）：

| 输入 | 输出 |
|---|---|
| `'2026-10-08 09:31:02.123456+08'` | `2026-10-08T01:31:02.123Z` ✅ |
| `'2026-10-08 09:31:02+08'` | `2026-10-08T01:31:02.000Z` ✅ |
| `'2026-10-08T09:31:02.123456+08:00'` | `2026-10-08T01:31:02.123Z` ✅ |

**结论**：`fmtTime.full()` 直接可用，**前端零特判、后端零改动**。原因在 `norm()`
（`fmtTime.js:8-12`）：它先 `.replace('T',' ')` 再 `new Date(...)`，**归一成空格分隔形态**
后 V8 的解析器对此完全容忍（含 6 位微秒截断与裸 `+08` 偏移——后者被当 `+08:00` 处理，
`01:31Z` = `09:31+08` 偏移正确）。`isNaN` 兜底分支不会触发。
⇒ **裁决点① 在原判中被降级为「隐性债，不阻断本批」**（见 §裁决点 ①）。

### 3.4 空态与错误态（三态必须分开，这是本批的诚实性要求）

| 态 | 判据 | 渲染 |
|---|---|---|
| 无冻结史 | 请求成功 + `events.length === 0` | `t('liveTask.freezeNone')`——**「无冻结记录」**，中性文案 |
| 无权限 | HTTP 403 | 复用既有 `_timeline === null` 同款模式（`:312`）：**不显示区块**或显示「无权限查看」，**不能显示成「无冻结记录」** |
| 拉取失败 | 其他异常 | `t('common.loadFailed')` + **可重试**（区块内小「重试」钮） |

**为什么必须分**：「没有冻结」和「看不到冻结」是完全不同的事实。既有代码在 `:312` 已经踩过
这个坑并留了注释（「403=无权限(null 与空时间线区分——防重启数假 0)」）——**本批照抄这个判据，
不要再发明一套**。（后端端点已挂 `read`，四角色都有 ⇒ 403 实际不会发生；但**代码要能表达这个区分**，
否则将来 `read` 一改，UI 会静默说谎。）

### 3.5 文案（`operator` 拼接串）

`manual_im` 的 `operator` = `"{web用户名}（IM 发起:{im操作人}）"`（`trading.py:217`）——
列宽 140px 下会溢。**`show-overflow-tooltip` + 不做截断逻辑**（el-table 自己处理）；
若嫌长，可在渲染层取 `split('（')[0]` 只显 web 用户名 + tooltip 给全串——**但这属于信息损失，
默认不裁**，主表给全串、靠 tooltip 撑。

## 4. 文件改动清单（5 个文件——裁决点①已裁定「马上改」，故含 1 个后端文件）

| 文件 | 改动 | 量级 |
|---|---|---|
| **`server/src/data_platform/freeze_event.py`** | **裁决点①定稿**：`:93` `str(r[5])` → `r[5].isoformat()`；`:94` 同 | **2 行** |
| `web/src/views/LiveTask.vue` | 展开行加冻结史区块 + `onExpandChange` 拉取 + 三态渲染 + 六列表 | ~60 行（含样式） |
| `web/src/locales/index.js` | zh `liveTask:` 块（`:366`）+ en 块（`:1792`）各加 ~10 键 | ~20 行 |
| `web/src/api.js`（可选） | 加 `getFreezeEvents = tid => api.get(\`/live-task/${tid}/freeze-events\`)` | 1 行 |
| `flow/任务/批76b-冻结史UI.md` | 本文件 + 交付记录 | — |

> **落码实况（2026-10-02 00:20，见 §8.2）**：实际改 **3 个源码文件**（`api.js` +5、
> `locales/index.js` +16、`LiveTask.vue` +97），净 **+109/-9**。与原计划差异两处：
> ① 本文件 §8.2 追加记录；② **`LiveTask.vue` 多出一个 `<style scoped>`**
> （`var(--warn-bg)` 不存在，见 §8.3 缺陷 4）——原计划「~60 行含样式」估得不准，
> 实际因**四列对齐 + 三态 + 行内解冻钮 + 5 个逻辑函数**而翻倍。

**后端 2 行的连带自查（落码第一步做，别跳）**：
1. `grep -rn "frozen_at" server/ --include=*.py` —— 确认**除 `list_events` 外无其他读点**；
2. `grep -rn "frozen_at" server/tests/` —— 确认测试断言不依赖 `str()` 形状
   （`test_batch76_freeze_event.py` 是主要嫌疑）；
3. **重启面**：`isoformat()` 改动需重启 `quant-web-api@quant` 才生效——**随本批上产同窗，不单开**。

**`api.js` 那 1 行是可选的**——`LiveTask.vue` 已有直接 `api.get('/log', {params})` 的先例（`:310`），
跟随即可。加进 `api.js` 的好处是语义命名 + 将来监控页复用；坏处是又多一个出口。
**倾向加**（冻结史是可预期的多消费面，且 `api.js` 是本仓既有惯例）。

**不动**：`web/src/router.js`（无新路由）、`permGroups.js`（无新权限键）、`TableShell.vue`、
`hub_worker.py`（写点零改动）、`collector.py`（视图 B 已否决）。

## 5. 验收清单

**行为级（真实数据前也能做）**：
1. 未展开时**零请求**——Network 面板确认页面加载只有 `live-task` + 每任务一条 `log`，**无 `freeze-events`**；
2. 展开 → 恰好一次 `freeze-events` 请求；折叠再展开 → **不重拉**（缓存命中）；
3. 空表渲染正常（prod 现在就是空的——**这就是「数据为空也正常渲染」的天然验证**）；
4. 手动构造一行（dev 库 `INSERT` 或 mock）验证六列 + 进行中高亮 + 时长计算；
5. 三态分离：临时把端点改到不存在的路径 → 应显「拉取失败」而**不是**「无冻结记录」。

**门（必须全绿，本仓既有闸门）**：
6. `bash scripts/check-tokens.sh` ← **内联 px/hex/font-size 不许升**（最容易撞）
7. `node web/scripts/check-locales.mjs` ← 重复键 + 注释吞键
8. **zh/en 缺键自查**（构建门**不查这个**，会静默回落）：
   `grep -c "frFrozenAt" web/src/locales/index.js` 应为 **2**（zh+en 各一）。
   **逐键数一遍，漏 en = 英文界面混中文，构建全绿零提示。**
9. `cd web && npm run build`（含 prebuild 两道门）
10. 全量 `pytest` + `ruff`（预期零影响——本批不动 Python；**跑了才知道**）

**真数据验证（10-08 开市后，本批的最终判据）**：
11. 若出现 `seq_gap` 自动解：**一行**事件（`frozen_at` + `unfrozen_at` 俱在，`method=auto_reconnect`）；
12. 若出现 `ts_gap` 人工解：**一行**事件（`method=manual_web`，`operator` = web 用户名）；
13. **重启解**：若上一进程冻结着退出 → 下次启动**补记一行 `restart`** ⇒ 表里**不应有 `unfrozen_at IS NULL` 的历史残留**；
14. 「无冻结事件」若在开市后仍恒真，**说明写点没跑通**——去 `journalctl` 查
    `冻结事件记账失败（不阻断冻结）` 告警（`hub_worker._record_freeze` 的 fail-soft 分支）。

## 6. 【2026-10-01 用户裁定】三案对比：视图 A / 视图 B / 独立页签

> 用户问：「a. 冻结和解冻入日志，通过类型筛选查看；b. 在运行状态页签，hub 卡片状态改为冻结。
> 这两个方案和单独页签相比，各有什么优劣势？」
> **先给结论，再给逐条账。**

### 6.0 结论先行

| 方案 | 判定 | 一句话 |
|---|---|---|
| **C · 独立页签**（监控页第四个 tab，全站视图） | ⚠️ **可做但数据不全** | 端点/分页都得新增；且它是**全站视图**，而本批需求 90% 是「这个任务怎么回事」 |
| **A · 入日志 + 类型筛选** | ❌ **否决（数据来源根本性错配）** | 冻/解事件**根本不在 `system_log` 里**——要么新造 `event()` 写点（再造一遍 F1），要么筛 `source` 但**重启解冻那条永远筛不出来** |
| **B · hub 卡片改冻结** | ❌ **否决（假方案）** | hub 卡片展示的是**数据源连接**（`md-hub·{acct}`），**任务冻结是 per-task 的，与 hub 无对应关系**——改了就是**语义造假** |
| **主案 · 任务页展开行**（§2.1） | ✅ **本批做** | 唯一「数据源天然对齐 + 零后端改动 + 权限梯度正确」的落点 |

**一句话理由**：A 和 B 都是**在错误的数据面上找位置**——A 找的是日志面（事件不在那儿），
B 找的是 hub 面（冻结不在那儿）。而 §2.1 的任务页展开行，数据面（`freeze_event.task_id`）本身
就是任务维度的，**零搬运**。

### 6.1 视图 A · 冻结/解冻入日志 + 类型筛选

用户设想：把冻/解事件写进运行日志，在 Logs 页用「类型」筛出来。

**✗ 致命问题一：事件根本不在 `system_log` 里。** 逐点证实：

- `system_log` 的唯一写入通道 = `log_sink._Sink`（`data_platform/log_sink.py:32-58`），它把
  **每条 `logging` 记录**落库，`module` = `record.name[:60]`（即 logger 名，如 `strategy_runner.hub_worker`），
  `source` = 进程级常量（live-task 是 `live:{tid}`，`main.py:333`；hub 是 `hub`，`md_hub/main.py:122`）。
- **F1 的事实写点走的是 `data_platform.freeze_event.record_freeze()`（直接 INSERT），不是 logger。**
  ⇒ 冻结事实**从不经过 `system_log`**，Logs 页**筛不到**，无论怎么筛。
- 想让它筛得到 ⇒ 必须在写点**额外补一条 `log_sink.event()`**（`log_sink.py` 有 `event()` 通道）
  ⇒ **等于把 F1 的写点再实现一遍**（双写），且引入经典双写不一致风险（一边成功一边失败）。
  **这是「为了一个筛选器而重建事实通道」，本末倒置。**

**✗ 致命问题二：即便补了写入，重启解冻那条永远缺失。** `_close_freeze("restart")` 的语义是
「上一进程冻结着退出，本次启动补记闭环」（`hub_worker.py:284-287`）——**它只 `logger.info`**，
而 `system_log` 的 sink level = `INFO`（`log_sink.py:34`）……**但注意 `logger.info` 只在该函数里
`if n:` 才打**（`freeze_event` 的 `close_freeze` 返回 0 时**静默无日志**）。
更关键：**「冻了几次」这个计数语义天然不属于日志**——日志是流（同一事实可能零条/一条/多条），
事实是账。**用日志筛事件 = 用流重建账**，重建不出来的部分（重启闭环、自动解冻）恰恰是审计最关心的。

**✓ 唯一真优势**：与既有告警流**同屏**。`frozen.stream` / `frozen.intercept` / `frozen.auto` /
`frozen.manual` 四码**确实会以告警形式落 `system_log`**（`alert_notify` → logger），所以
「日志页能看到冻结告警」**已经成立**（无需本批做任何事）。但那是**告警**，不是**事件账**：
告警没有 `unfrozen_at`、没有 `operator`、没有冻结时长、`frozen.auto` 只说明「自动解了」不说明「冻了多久」。

**权限面**：Logs 页门 = `user_mgmt`（`auth_routes.py:971`，admin-only），而冻结史端点门 = `read`。
**走 A 案 = 把冻结史从「四角色可见」降级成「admin-only」**——与批 77 的「读事实 / 写控制」分档冲突。

### 6.2 视图 B · hub 卡片状态改冻结

用户设想：监控页运行状态页签，hub 卡片的状态标签从「在线」改成「冻结」。

**❌ 这是假方案——数据面和语义面都不成立。** 逐点证实（`ConnectionCards.vue` + `collector.py`）：

| 面 | 事实 |
|---|---|
| 卡片是什么 | `md-hub·{{ acct }}`（`ConnectionCards.vue:12`）= **数据源连接实例**，`acct` 是 `account_id` |
| 卡片数据源 | `snap["hubs"][acct]`（`collector.py:249-268`）= Valkey 键 `quant:hb:md-hub:{acct}` 的 `gen/subs/ticks/bars/tick_age` |
| **有没有 frozen 字段** | **没有。** hub 心跳**不写 frozen**（`collector.py:249-258` 字段清单里无此项） |
| 冻结在哪 | `snap["tasks"][tid]["frozen"]`（`collector.py:285`）= 键 `quant:hb:task:{tid}` 的 `frozen` 字段（`hub_worker.py:473` 写 `int(frozen.get("now"))`） |
| 卡片已展示了吗 | **早就展示了**——`ConnectionCards.vue:24-32` 的 **task 卡片**已经有 `tk.frozen ? 冻结 : 运行中`（`sysmon.frozen` 词条现成） |

**三重不成立**：

1. **粒度不成立**：hub 是**账号级**（一账号一 hub，服务该账号下**所有**任务）；冻结是 **per-task** 的。
   一个 hub 下 3 个任务，2 个冻结 1 个正常——**hub 卡片该显示什么？** 显示「冻结」就把正常的那个
   也诬告了。**没有正确的聚合语义**（除非显示「2/3 冻结」，那是另一套设计，且仍是任务维度信息）。
2. **事实不成立**：worker 的 `frozen["sticky"]` 是**进程内状态**（`main.py:243`），
   `frozen["now"]` 含盲视动态态（`hub_worker.py:452`）。**hub 进程根本不知道任务冻没冻**
   ——它只管行情分发。改 hub 卡片 = 让 hub 卡片**显示它拿不到的数据**。
3. **已经做了**：task 卡片（`:24-32`）**就是**用户描述的那个东西，且是**实时态**（30s 轮询
   `getHealthComponents`，`:49/56`）。**B 案的真实诉求已被满足**——用户可能没注意到那张卡片。

**⚠️ 但 B 案暴露了一个真缺口（值得记）**：task 卡片显示的 `frozen` 是**瞬时布尔**，
**它闪一下就没有了**——30s 轮询下，一次 3 分钟的自愈型冻结**可能被整个跳过**，
而人工解冻前后的对账窗口**更是完全看不见**。**这正是 F1 立表要解决的问题**：
`freeze_event` 是**持久账**，卡片是**瞬时灯**。⇒ **B 案的正解不是改卡片，是给卡片加一个「历史」入口**。

### 6.3 独立页签（C）· 严格账

本方案 §2.3 原本假设的就是它。三案对比下它的位置更清楚了：

**✓ 真优势（两条，都是 A/B 给不了的）**
1. **唯一能回答「今天全站有几个任务冻了」的形态。** A 案筛日志给不了（事件不在日志），
   B 案给不了（hub 粒度错）。**跨任务横向视角只有独立视图能做。**
2. **与既有 Observe 页签骨架零冲突**：`Observe.vue:14-19` 是 `TabsShell` + 三页签
   （status/logs/audit），加第四个 tab 是既有模式（`Observe.vue:25-29` 就是加 tab 的现成写法）。

**✗ 真代价（比 §2.3 说的更重）**
1. **必须新增后端端点**：`list_events(task_id=None)` 支持全量（`freeze_event.py:81`），
   但**没有端点暴露它** —— `trading.py` 只有 per-task 那条。新增 `GET /api/live-task/freeze-events`
   ⇒ **破坏 §0「零后端改动」**。
2. **必须补分页**：全量 `list_events` 只有 `limit` 无 cursor。`system_log` 侧的 cursor 模式
   （`auth_routes.py:1014-1026`，`epochµs|id` 双键）是现成参考，但**那是要写的代码**。
3. **权限门要选**：全站视图挂 `read` 还是 `system_config`？挂 `read` = 与 per-task 一致（推荐）；
   挂 `system_config` = 与 Observe 页其他 tab 一致。**两个都对，得选一个并说明理由。**
4. **需求未经真数据验证**：prod 现在**零冻结事件**（批 76 刚上产，10-08 才开市）。
   做全站视角前，**没人知道事件密度**（一天 0 条还是 50 条？）——分页设计、默认时间窗、
   是否需要「仅进行中」开关，**全都依赖这个不知道的数字**。

### 6.4 三案能力对照表（核心交付）

| 能力 | A 入日志筛选 | B hub 卡片改 | **主案 任务页展开行** | C 独立页签 |
|---|---|---|---|---|
| 数据真实性（事件真来自 `freeze_event`） | ❌ 不在日志里 | ❌ hub 无此数据 | ✅ 直读表 | ✅ 直读表 |
| 粒度正确（per-task） | ✅（若双写） | ❌ hub 是账号级 | ✅ 天然 | ⚠️ 全站，需按任务分组展示 |
| 冻结时长（`unfrozen_at - frozen_at`） | ❌ 无解冻端时刻 | ❌ | ✅ | ✅ |
| 操作者 / 通道（`operator`） | ❌ | ❌ | ✅ | ✅ |
| **重启闭环**（`restart` 补记） | ❌ 无日志 | ❌ | ✅ | ✅ |
| 进行中事件（`unfrozen_at IS NULL`） | ⚠️ 靠告警间接推 | ⚠️ 瞬时灯会漏 | ✅ 高亮 | ✅ |
| 跨任务横向视角 | ⚠️ 日志面杂乱 | ❌ | ❌ | ✅ **唯一** |
| **后端改动** | 需双写（重） | 需 hub 加字段（重） | **零** ✅ | 需新端点+分页（中） |
| **前端改动** | Logs 页加筛选维度 | ConnectionCards 改语义（错） | 展开行 ~60 行 | Observe 加 tab + 组件 ~150 行 |
| 权限门与批 77 一致 | ❌ 降级 admin-only | — | ✅ `read` | ✅（选 `read` 即一致） |
| 实时性 | 日志 5s 落库 | 30s 轮询 | 展开时拉 | 展开/轮询 |
| 需求是否已验证 | — | — | ✅ prod 空表即验证 | ❌ 密度未知 |

### 6.5 推荐路径（**组合，不是二选一**）

**本批只做主案（任务页展开行）**，其余挂账——理由三条：

1. **A 案不是「另一个位置」，是「另一个数据源」**，且那个源（`system_log`）**装不下事实**
   （§6.1 两条致命）。要让它装得下，得**先**在 F1 写点补双写——**而双写一旦上去，主案和 C 案
   都不需要它**。**为一个被否决的视图去污染事实写点，是净负收益。**
2. **B 案的真实诉求（「我想一眼看出来冻没冻」）已被 task 卡片满足**（`ConnectionCards.vue:24-32`），
   用户没看到而已 ⇒ **本批要做的不是改卡片，是把卡片和事实接上**：
   **挂账项** = task 卡片加「历史」小入口（点开跳任务页展开行，或就地弹冻结史）。
   **但它依赖主案先落地**（先有展开行才有跳转目标）⇒ **顺序上必须主案先行**。
3. **C 案是主案的严格超集**，多出来的能力只有一项（横向视角），而那一项**需要真数据才能设计**。
   ⇒ **等 10-08 开市跑出一周事件密度，再决定做不做、怎么分页**。

**一句话**：**主案 = 唯一零成本且数据面正确的落点；A/B 是在错面上找位置，C 是主案加上一层
尚未被数据检验的横切面。**

---

## 7. 【2026-10-01 23:12 追问】任务列表 + 标题栏告警图标

> 用户问：「在运行状态页签最后面加一个任务列表，或者单独加一个任务列表页签，把冻结/解冻信息和
> 当前状态在任务列表中展示，逻辑上成立吗？然后如果有被冻结的任务，就在标题栏展示告警图标，
> 点击进入对应页面？」

### 7.0 结论先行

| 诉求 | 判定 | 一句话 |
|---|---|---|
| **7A 任务列表带「当前状态 + 冻结史」** | ✅ **成立，且是本批最好的形态** | 数据面**完全对得上**（`GET /live-task` 已带实时态）——**比我原主案（展开行）更好** |
| **7B 标题栏告警图标 → 点击进冻结任务页** | ⚠️ **能力已存在但需接；且「冻结为主」是错的目标态** | 标题栏**铃铛+角标已存在**（admin-only，→`/observe`）；但「当前有冻结」**只存在于交易时段**，非交易时段恒假 |

### 7.1 7A：任务列表——**成立，而且确实比我原定的展开行更好**

**为什么数据面成立**：`GET /api/live-task`（`trading.py:25-65`）返回的字段**已经包含**用户要的全部：

| 用户要的 | 字段 | 来源 |
|---|---|---|
| 当前状态 | `status`（running/stopped/error）+ `frozen`（bool） | DB `live_task.status` + 心跳 `quant:hb:task:{tid}` 的 `frozen` |
| 实时性 | `md_mode` / `lag` / `bars` / `hb_age_s` | 同上心跳（`trading.py:61-65`） |
| 冻结/解冻信息 | **需补一次 freeze-events 拉取** | `GET /live-task/{tid}/freeze-events` |

⇒ **「当前状态」零成本**（`LiveTask.vue` 已经在用这个端点，`api.js:160`）；
**只有「冻结史」要额外拉**——但那和 §2.1 展开行是**同一个请求**，不是新成本。

**⚠️ 「当前状态」有一个致命的边界：非交易时段 `frozen` 恒 `false`。** 逐点证实：

1. `frozen` 值来自心跳字段 `frozen`（`hub_worker.py:473` 写 `int(frozen.get("now", False))`）；
2. `frozen["now"]` = 盲视动态态 **或** sticky（`hub_worker.py:461`），而盲视判定含
   `sess_now = _in_mkt_session()`（`:455`）——**盘外 `bar_stale` 恒假**；
3. **心跳键 TTL 90s**（`HeartbeatWriter(r, hb_task_key, ttl=90)`，`hub_worker.py:428`）——
   任务停止/进程退出后 90 秒键自动消失 ⇒ `h.get("frozen")` 取不到 ⇒ `frozen=false`；
4. 且**进程退出时 sticky 随进程内存一起没了**（`frozen` 是进程内 dict，`main.py:243`）；
5. **启动补记**（`_close_freeze("restart")`，`hub_worker.py:287`）会在下次启动把遗留 open 事件闭环
   ⇒ **库里也不会有 `unfrozen_at IS NULL` 的残留**。

**推论（重要）**：**「当前有冻结」在盘外不可知、且事实上必然为假**——因为冻结状态**不能跨进程存活**，
启动即闭环。所以列表里那个「当前状态」列在非交易时段是**诚实的 false**（不是 bug）。
**真正恒久可查的是「冻结史」**（`freeze_event` 表），**这正是 F1 立表的意义**。

**落点选哪个（回答用户的「运行状态页签内」vs「独立页签」）**：

| | 运行状态页签内追加 | 独立页签 |
|---|---|---|
| 位置合理性 | ⚠️ Observe 页门 = `system_config`（**admin-only**，见 `system.py:199/271`） | ✅ 可挂 `/live-task` 伴生页 |
| 与既有页关系 | 运行状态页签已有「资源/服务/连接」三卡，任务是第四块——**塞得下但偏堆砌** | 语义独立 |
| 权限面 | ❌ **admin-only ⇒ 与「冻结史挂 `read`（四角色可见）」冲突**（同 §6.1 A 案的病） | ✅ 可挂 `read` |

⇒ **推荐：不动 Observe 页**。任务列表**已经存在**（`LiveTask.vue`，门 = 无 `meta.perm` ⇒ 登录即可 +
按钮按 `canLive` 显隐）。**用户的真诉求 = 在既有任务列表里「看得见冻结史」** ⇒
**= 本方案主案（展开行）**。若嫌展开行要点击，**可加一列「最近冻结」摘要列**
（`冻结 3 次 · 最近 10-08 09:31`），但那**要么 N+1 拉取、要么后端加聚合字段**（见 §2.4 否决理由）。

**⇒ 7A 的净结论**：**成立，但落点就是 `LiveTask.vue`（既有页），不是 Observe 页**。
「运行状态页签内加任务列表」= **在 admin-only 页里重造一个已存在的列表**，净负收益。

### 7.2 7B：标题栏告警图标——**能力已存在；但「冻结」是错的目标态**

**⚠️ 先纠正一个可能的误解：标题栏那个铃铛已经有了。** `MainLayout.vue:15-19`：

```vue
<!-- 告警（批22：admin-only，角标=活跃告警数，点击进系统监控页） -->
<el-badge v-if="role === 'admin'" :value="alertCount" :hidden="!alertCount" :max="99">
  <IconBtn :icon="Bell" :title="t('sysmon.title')" @click="$router.push('/observe')" />
</el-badge>
```
- 计数源 = `GET /api/system/alerts`（`system.py:270-286`）= Valkey 电平键 `quant:hm:state:*`；
- **且它已经数得到冻结**：health monitor 的 **R5 规则 `task_blind`**（`monitor.py:186-190`）
  对 `snap.tasks[tid].frozen == 1` **逐任务产出 warning 电平** ⇒ **心跳 frozen=1 时铃铛会亮**。
- 30s 轮询（`MainLayout.vue:255`）。

**⇒ 「有被冻结的任务 → 标题栏亮图标」这件事，机制上已经成立。** 用户可能没注意到。

**但两个真缺口必须说清**：

1. **点击目标错**：现在点铃铛 → `/observe`（admin-only 的监控页），**不是「对应的任务」**。
   用户要的是「点击进入对应的页面」——**当有多个冻结任务时，一个铃铛指向一个页面本身就不够**，
   需要的是**列表/下拉**（点开列出「哪个任务冻了」→ 逐条跳）。这是**真需求，值得做**。
2. **`task_blind` 是 `warning` 且与「冻结」不完全等价**：它覆盖 `frozen["now"]`（含盲视动态态），
   而盲视**不是** F1 记的三种 sticky 冻结（`hub_worker.py:452` 明写「盲视…不落 F1」）。
   ⇒ **铃铛亮 ≠ 有冻结事件**（可能是盲视，数据恢复自动解）；**铃铛不亮 ≠ 无冻结事件**
   （sticky 冻结在盘外进程已退出 ⇒ 心跳没了 ⇒ 铃铛灭，但**表里有未闭环的账**）。
   **两个信号不同源、不同生命周期，不能互相冒充。**

**⇒ 7B 的正解**：**不是新建一个图标，是给既有铃铛补一个「冻结任务」的视图/下拉**——
但要挂 **`read`**（冻结史四角色可见）而铃铛本身是 `admin-only`（`system_config`）。
⇒ **两者权限面不一致**：要么把冻结项做成**独立的、挂 `read` 的第二入口**，
要么明确「冻结告警保持 admin-only」（与 `quant:hm:state` 其余告警同面）。

### 7.3 汇总：本批应做什么（在 7A/7B 之后重排优先级）

| 优先级 | 事项 | 理由 |
|---|---|---|
| **P0** | **主案：`LiveTask.vue` 展开行冻结史**（§2.1，原定） | 零后端、权限正确、数据面天然对齐 |
| **P0'** | **7A 的收敛形态 = 就是 P0** | 「既有任务列表 + 冻结史」= 展开行；**无需新建列表** |
| **P1（挂账，依赖 P0）** | **挂账③：task 卡片「历史」入口**（§6.2 暴露） | 接通瞬时灯与持久账 |
| **P2（挂账，独立议题）** | **7B：铃铛下拉列冻结任务** | 真需求，但**权限面要与 `read` 对齐**，且需先有 P0 的落点可跳 |

**一句话**：**7A 你说的那个列表已经存在（`LiveTask.vue`），要补的是「冻结史」那一块——就是主案；
7B 你说的那个图标也已经存在（铃铛+角标，且已经能数冻结），要补的是「点击后能到对的任务」——
但它的权限面（admin-only）和冻结史的权限面（`read`）不一致，得先定一个。**
**两个诉求都不是「新建」，都是「把已有的接上」——和 B 案的结论完全同源。**

---

## 8. 【2026-10-02 00:20 落码】实现记录 + 权限模型三问收口

### 8.1 权限模型：三问三答（**结论=零改动**，但每条都有实证依据）

用户连问三题，逐题核证后收口。**净结论：权限模型自洽，本批不动任何权限键。**

| 问 | 用户原话（要点） | 结论 | 实证 |
|---|---|---|---|
| ③ | 「每个任务能对应到某用户创建，创建者是不是该有解冻权？」 | **否。创建者不是权限锚点。** | `live_task.owner_username`（迁移 `0074`）确实存在，但**唯一用途是市场风控**：`owner_username` → runner 注入 `strategy.operator`（`main.py:232`）→ `order["operator"]` → `RiskControl.check_order`（`strategy.py:431` 注释明写「服务端钉死——Signal/策略参数无此通道」）。**从无任何权限判断读它。** 按创建者授权会在两处崩：① 同 trader 建的任务落到他人账号（「建的人」≠「管账号的人」）② 多 trader 共管一账号。正解锚点=`account_id` 授权，**不是** 创建者。 |
| ④ | 「analyst 也能建回测/实盘测试任务，都不发生交易，所以不存在冻结概念，对吗？」 | **对一半 —— 「实盘测试」这个词是陷阱。** | **回测侧对**：全仓冻结写入点**只有 3 处**（`hub_worker.py:324/347/373`），回测链路（`strategy_framework/backtest.py`、`web_api/routes/backtest.py`）`freeze`/`sticky`/`HeartbeatWriter` **零命中**——冻结的物理定义是「实时流断了/失真了不敢放 BUY」，回测读已落库历史数据，无此问题。**但「实盘测试」≠ `live_task`**：analyst 权限 = `{read, strategy_control, data_sync}`（`0056` seed，`0074` 还删了它越权的 `system_config`）——**analyst 从无 `live_control`，也建不了 `live_task`**。批 77 §一 已把 `POST /api/live-task` 及起停/删除/解冻全判 analyst ❌。「实盘测试」指的是**研究面**的策略验证（`strategy_control` 域），不是实盘任务。**AI 前一回复据「analyst 建的 live_task」立的反例 2 已撤回——批 77 早已堵住。** |
| ⑤ | 「我（admin）建新角色，授权它操作某市场实盘，但不给解冻权——合理吗？」→ **随后用户自己修正**：「有某市场实盘操作权就该能解冻，**应该合并而不是拆分**」 | ✅ **用户最终裁定正确 —— 合并，不拆键。** | `live_control` 现挂 **8 端点**（`strategy.py:160/185` + `trading.py:71/145/163/178/195/239`），**全是实盘面操作，语义完全同质**。而 `market_op` 是**独立维**（`market_op_allowed()` 查 `dimension='market_op'`，挂 `risk.py:286` 的 `check_order`）管「哪个市场」。故：<br>**允许操作实盘任务 := `live_control`(能力) ∧ `market_op[market]`(市场)**<br>**解冻与起停需要的条件一字不差** ⇒ 拆键是**凭空发明区别**，拆完两键 seed 永远一致（同给同收）。<br>**判据是「面」不是「动作危险梯度」**：批 77 拆 `strategy_control`→`live_control` 对，因为拆的是**面**（研究面 vs 实盘面）；再拆 `live_control` 内部**已无第二个面**。两次判断同一判据。 |

**AI 撤回项（诚实记录）**：① 「`live_control` 打包三件危险方向不同的事」——**错**，8 端点同面同方向；② 「拆键」建议——**伪解法**；③ 「值班死角」论证——**建立在拆分之上，地基不成立**。
**AI 保留项（与拆不拆无关）**：① `owner_username` 非权限锚点（`market_op` 是 role 级，与创建者无关）；② 解冻是**不可撤销的风险接受**（端点注释原话「操作者显式接受当前数据状态」）——但这只构成**留痕**理由，而留痕**已有**（`freeze_event.operator` + `manual_web/manual_im`）。
**用户模型（四维）**：能力 `live_control` × 市场 `market_op` × 账号 `account_allows`（开仓级，SELL 豁免）× 页面 nav。

### 8.2 落码清单（3 文件，+109/-9）

| 文件 | 改动 |
|---|---|
| `web/src/api.js` | +`getFreezeEvents(id, limit)`（perm `read`）+`unfreezeLiveTask(id)`（真端点） |
| `web/src/views/LiveTask.vue` | 展开行第二区块（冻结史六列 + 进行中高亮 + 三态分离 + 行内解冻钮）；`onUnfreeze` **改调真端点**；`preloadFreeze`/`loadFreeze`/`freezing`/`frozenFor`/`onExpand`；操作列解冻钮判据换持久账；新增 `<style scoped>` |
| `web/src/locales/index.js` | +20 键 × 双语（50/50 对齐）；**并修正既有 2 键**——`unfreeze`/`confirmUnfreeze` 原文「重启解冻 / Restart & unfreeze」**已因本次改动失实**（不再重启），改为「解冻 / Unfreeze」+ 准确语义说明 |

### 8.3 落码中自查出的 4 个缺陷（**单测捕获，非目视**）

写了一个 24 断言的纯逻辑单测复刻 `freezing`/`frozenFor`/`onExpand`/`preloadFreeze`，**首轮 3 红**——全是我自己刚写的 bug：

| # | 缺陷 | 根因 | 后果 |
|---|---|---|---|
| 1 | `onExpand` 不认 boolean 重载 | EP `expand-change` 有**双重重载**（`defaults.d.ts:331-332`：`(row, T[])` 与 `(row, boolean)`）；我把 `true` 包成 `[true]`，`r?.id === row?.id` 恒 false | 一旦落到 boolean 重载，展开**永不拉取** |
| 2 | `preloadFreeze` 判据写反 | `_freeze === undefined \|\| !freezing(x).length` = 「没拉过 **或** 没有未闭合」⇒ 已冻行 `_freeze` 有值且 `freezing().length>0` ⇒ 条件 false | **已冻行永不刷新** ⇒ 时长冻住、解冻后不消失 |
| 3 | 同上的另一面 | 干净行 `_freeze=[]` ⇒ `!freezing().length`=true ⇒ **每 30s 重复请求** | 请求放大 N 倍（N=运行中任务数） |
| 4 | `var(--warn-bg)` 不存在 | tokens.css 只有 `--warn`（文本档）/`--warn-fill`（填充档） | **静默透明**——CSS 无报错，高亮失效而无人知 |

**修法**：① 分流 `Array.isArray` → `some(id)`／`=== true`；② 归正为 `_freeze === undefined \|\| freezing(x).length`；③ 同上（一次写对两处）；④ 删 inline style 改 `<style scoped>` + `color-mix(in srgb, var(--warn-fill) 14%, transparent)`——**不引新 hex**（令牌门只许减不许增），也不依赖不存在的 token。

**另修一处时序缺陷**：`onUnfreeze` 原本在 POST 后立即 `loadFreeze` —— 但后端**只写 Valkey 请求键**，worker **5s 钩子**（`hub_worker.py:509-513` `GETDEL`）消费后才闭环 F1 ⇒ 立即重拉**必然仍显「进行中」**，用户会以为没生效。改为**只提示「已下发（5 秒内生效）」，不立即重拉**；闭环交给 `load()` 的 30s 轮询 → `preloadFreeze` 对有未闭合事件的行重拉自然带出。

> **方法论**：这 4 个都不编译报错、不冒烟失败——**只有跑断言才现形**。第 4 个尤其典型（CSS 静默失败）。
> 教训：**新写的前端纯逻辑，落码即单测**（复刻函数即可，不需跑组件），比目视可靠。

### 8.4 验收证据

| 项 | 结果 |
|---|---|
| 令牌门 | ✓ PX=39≤43 HEX=18≤18 FS=0≤0（**无新增字面量**） |
| locales 门 | ✓ 无重复键 / 行内注释吞键 0 / **en-zh 键集对称 1784** / 占位符一致 |
| 词条死键 | ✓ 新增 20 键**全部被引用**（逐键 `grep -c` = 1） |
| 逻辑单测 | ✓ **24 passed 0 failed** |
| 前端 build | ✓ built in 29.41s |
| 后端全量 | ✓ **1805 passed, 1 skipped**（与改前基线**逐数一致**，零回归） |
| 后端相关 | ✓ `test_batch76_freeze_event.py` + `test_deploy_blast_radius.py` 14 passed |

**上产**：非迁移（不触 DDL 门）、需重启 `quant-web-api@quant`——随下个部署窗，不额外开窗。

## ⚖ 裁决点（**① ② ③ 均已由用户 2026-10-01 22:18 裁定**；④⑤ 见 §8.1）

**① 时刻列序列化契约** —— ✅ **「马上改」已落码 + 验绿**（`freeze_event.py:93-99` `str → isoformat()`）



**① `frozen_at` / `unfrozen_at` 改 `isoformat()`** —— ✅ **已裁定「马上改」，且已落码+验绿**：

**改动**（`server/src/data_platform/freeze_event.py:93-99`）：
```python
"frozen_at": r[5].isoformat() if r[5] else None,      # 原 str(r[5])
"unfrozen_at": r[8].isoformat() if r[8] else None,    # 原 str(r[8])
```
并补了 docstring 说明「勿用 `str(datetime)`」的原因（防后人改回去）。

**实测契约差异**（Python 3.10 + `Asia/Shanghai` tz，模拟 psycopg timestamptz）：

| | 输出 | 前端 `JSON.parse` 后 |
|---|---|---|
| `str()` | `2026-10-08 09:31:02.123456+08:00` | 空格分隔（非 ISO） |
| `isoformat()` | `2026-10-08T09:31:02.123456+08:00` | **标准 ISO-8601** ✅ |

**连带自查结果（三项全过）**：
1. **读点扫描** `grep -rn "frozen_at\|unfrozen_at" server/src/` ⇒ 消费点**仅两处**：
   ① `trading.py:231` 端点（直接透传）；② `im_bot/feishu_client.py:441`
   `if not e["unfrozen_at"]`（**只做 None 判真，不碰格式**）⇒ **零破坏**。
2. **测试断言扫描** `grep -rn "frozen_at" server/tests/` ⇒ 断言全是 `is None` / 真值判
   （`test_batch76_freeze_event.py:51,56,111`），**无一依赖字符串形状**。
3. **ruff**：`./venv/bin/ruff check src/data_platform/freeze_event.py` ⇒ **All checks passed**。

**验证运行**（用**项目自带 venv** `server/venv/bin/python`，Python 3.10.21）：
`tests/test_batch76_{freeze_event,freeze_worker,unfreeze_surface}.py` ⇒ **39 passed**（27.20s）；
**全量** `tests/` ⇒ **1805 passed, 1 skipped**（75.54s）——**与改动前基线逐数一致，零回归**。

> ⚠️ **环境坑记录（本沙箱）**：`server/venv/` **一直存在**，但一开始用 `find -maxdepth` 没搜到
> （venv 目录 `bin/` 下只有 `ruff` 等真文件，`python` 是指向系统解释器的软链，`-type f` 与
> `pyvenv.cfg` 搜索路径都没命中）。**正确判据 = `ls server/venv/bin/`**（或直接
> `./venv/bin/python -m pytest`）。踩了一次：先用系统 `python3` 跑出 `ModuleNotFoundError: psycopg`
> 假故障，还被误导去建了个隔离 venv 装依赖（**全是无用功——项目 venv 本来就有全套依赖**）。
> **教训入 SKILL：本仓跑测试/ruff 一律先探 `server/venv/bin/`，不要拿系统 python。**

- **成本**：非迁移（不触 DDL 门）、需重启 `quant-web-api@quant`——**随本批上产同窗，不额外开窗**。
- **遗留**：`watermark` / `gap_target_ts` 是 epoch 秒字符串（`_epoch_key` 产物）——**本次不动**
  （它们是水位快照不是时刻，且只在 tooltip 里出现；改它们要动 worker 侧写点，收益远低于成本）。

**② 视图位置** —— ✅ **裁定「另给两方案」并已对比** ⇒ **结论 = 主案（任务页展开行）**，
A/B 否决、C 挂账，理由与账见 **§6**（含 §6.4 对照表、§6.5 推荐路径）。

**③（新增·由 §6.2 暴露）task 卡片加「历史」入口** —— ⚠️ **挂账，依赖主案先行**：
- 现状：task 卡片 `frozen` 是**瞬时灯**（30s 轮询），**短冻结会被整跳**；
- 正解：卡片加小入口跳/弹冻结史（`freeze_event` 持久账）；
- **顺序**：主案（展开行）落地后才能挂——**本批不做，记入 `flow/待办.md`**。

## 参考（≤3）

- `server/src/data_platform/freeze_event.py`（读写单点 + 字段真源 + 控制面）
- `server/src/web_api/routes/trading.py:176-234`（解冻三端点：预览/执行/事实查询——三档权限对照）
- `web/src/views/LiveTask.vue`（落点：展开行既有区块 `:44-62` + 惰性拉取先例 `:307-313` + 权限注入 `:155-159`）
