# 批 116 · crypto 数据层（剩余范围）

> 立项 2026-10-09（主会话补决策后立项）。**2026-10-09 C 组步 2 双盲审 P0-1/P0-2 返工重写**：原版按 10-05 研究稿把批 101/101b/102b 已落地项当待做（83b 教训二犯），且引用了不存在的 `Temporality.continuous` 枚举。本版按库/真机实测重写现状与剩余范围。
> **2026-10-20 凌晨 v2（步 0 全闭环后收窄）**：产出 1 缺跑排查+补拉已全闭环（代理出口修复＝michael SOCKS 服务 + proxy_config 切本地隧道，2026-10-19/20 深夜；游标 20261006→20261018 补拉 3915 行实证）；剩余范围＝产出 2/3/4。步 1 方案随 v2 定稿：产出 3 时间轴裁定「维持现状」（判据见 §产出 3）；产出 2 盘中粒度＝用户裁定点；产出 4 重试预算小改。
> **2026-10-20 v3（步 2 双盲审过门）**：A 2P0+5P1/B 4P1 全处置（同判 flow/稳定性检查/盲审批116方案-同判综合.md 权威）——retention 列名修正（start_floor 已死列）/重试仅改 _MAX_RETRY/配套五件齐/压力按档分列/异步派发并入/manual 出口写明/契约重写全。
> 关联：批 101（币安日线）/ 101b（币安盘中）/ 102b（OKX 日线，五证上产 `202610061532-f5d0e55`）/ `flow/decisions.md` 2026-10-09 决策 B（已修正）

## 目标

crypto 数据层的**剩余缺口**收口：① okx_perp_daily 10-08/10-09 晨档缺跑排查（新发现，见现状 #5）；② OKX 盘中粒度（hourly/1min/15min，如需）；③ crypto 时间轴表达重裁（`streaming` vs 契约层扩枚举——设计变更须设计先行）。

## 现状（已实证 · 2026-10-09 对库/真机核实）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | 币安 + OKX **数据源层已完成**：adapter（`binance_adapter.py`/`okx_adapter.py`）、三注册表、接口层（`interfaces/binance_perp.py`/`okx_perp.py`） | `server/src/data_platform/adapters/`、`interfaces/` |
| 2 | 5 个 crypto 同步项已配置且 enabled：`binance_perp_{daily,hourly,1min,15min}` + `okx_perp_daily`；`start_floor` 已回填（daily 2019-09-08 / okx 2020-01-01 / hourly 2026-09-29 / 分钟 2026-10-05） | dev 库 `sync_config` 实查；批 102b 迁移 0137 五证上产 |
| 3 | 死构件 `data_increment_crypto` 已随批 101 退役 | `scheduler/app.py:154` 仅存注释；`tasks.py` grep 零命中 |
| 4 | prod 运行证据：`binance_perp_daily` 今晨 08:51 成功（1004 标的，拉/存 914 行，失败 0，游标 20261007）——08:38 僵尸复位（1423 min 未更新＝10-08 08:55 起挂）后重跑成功 | prod worker journal 2026-10-09 |
| 5 | `okx_perp_daily` 最近可见成功＝**10-07 08:44**（485 标的全存，排除段登记 2 条落 sync_gap）；10-08/10-09 晨档**无执行痕迹**（无成功行、无僵尸复位行；今晚调度器显示其「未到周期」⇒ next_run 已滚至 10-10）。**复现**：`quant-journal -u quant-celery-worker@quant.service --since -72h -n 300000 | grep okx_perp_daily`（ansible ad-hoc）；或查 `sync_log` `id='okx_perp_daily'` 最新行。**库位注（v3）**：本表 10-09 快照与 v2 头「游标 20261006→20261018」均为 **prod** 证据；**dev 永不可达 OKX**（出不了网，bar_1d OKX 段恒 0 行、sync_log 恒 error——okx_adapter docstring 已声明），prod relay 才是可达面 | prod worker journal -72h（2026-10-09 20:29 取证） |
| 6 | OKX **盘中粒度未做**：`okx_perp_hourly/1min/15min` 全仓 grep 零命中 | grep `server/src/` |
| 7 | `Temporality` 无 `continuous` 枚举（仅 historical/snapshot/streaming）——决策 B 该子句已撤回 | `quant_common/contract.py:44` |
| 8 | OKX 源重试预算偏薄（102b 挂账：7/485 标的 3 次退避后仍 ConnectionReset） | `flow/任务/批102b-OKX数据层.md` 待办注记 |

## 步 0 排查结论（2026-10-09 深夜落账）

**A. okx_perp_daily 10-08/09 晨档缺跑——根因定案：OKX 代理出口不通（infra）**
- 证据链：① 调度器两晨档**确有触发**（10-08 08:43:57 / 10-09 08:43:59 `n_triggered=2` 含 okx，内联跑 16-18s）；② 无 handler 完成行 ⇒ `list_symbols()` 阻塞 ~16s 后 `raise RuntimeError`（`engine.py:1307`，enum_hint 自述「instruments 接口不可达（含代理出口未配/不通）」）；③ `sync()` except（`:470`）落 `sync_log` error 行 + 告警 + `last_sync_ts` 刷新（⇒ 调度器下轮「未到周期」，无重试风暴——机制按设计工作）；④ **prod 直连 OKX 实测 `curl rc=124` 超时**（OKX 大陆被墙，批 102a 设计须走代理出口；代理出口 10-08 起不通）。
- **数据面**：游标停 20261006，10-07 起缺数据（游标契约会自动补——代理恢复后下一轮从 10-07 重拉，`cursor_upto`＝实际取到数据的最后一日）。
- **处置**：代理出口修复属 **infra/装位**（同批 111 infra 两则，交办 michael/用户）；代码面**无需修**——失败可见性（sync_log error + 告警）按设计工作。附产品缺陷：调度器 16s 内联阻塞（okx 不在 `_SYNC_ASYNC_DISPATCH` 异步清单）——**顺手改进候选**：把 crypto 日线档加进 `_SYNC_ASYNC_DISPATCH` 防调度轮长阻塞（beat 5min 一轮被占 16s+）。
- 待办：代理恢复后验证一轮自动补拉（游标 20261006 → 追平）。

**B. 其余步 0 项**（时间轴重裁 / OKX 盘中需求确认）：未做，随批推进。

**步 0-A 后续进展（2026-10-10 二次复审补记）**：okx_perp_daily 已于 10-10 08:44 **自动补拉成功**（485 标的、拉 1455 行、失败 0、游标推进 20261009——prod journal 实证；infra 挂账保留待 michael 根修代理出口）。产出 1 的「补拉缺口」执行面已达成，本批剩余焦点＝盘中粒度需求确认 / 时间轴重裁 / 重试预算。

## 依赖（就绪）

批 101/101b/102b 全部 ✅（上产）；`sync_gap` 排除段机制 ✅（批 109）；102a 代理体系 ✅（**但出口当前不通**，见步 0-A——infra 修复前置）。

## 引用（设计 / 方案）

- `flow/方案/多市场数据接入与代理体系-设计-20261006.md` §二/§四 —— crypto 接入设计真源。
- `flow/decisions.md` 2026-10-09 决策 B（修正版）—— start_floor=NULL 保留窗口、键 (symbol) 仍有效；时间轴表达待重裁。

## 产出

1. **步 0 · okx_perp_daily 缺跑排查（最优先，数据完整性）**：查 10-08/10-09 晨档为何未触发/无痕（对照 binance 僵尸复位路径——okx 是否也挂了但复位行丢失？`sync_log`/`sync_gap` 落库核对），补拉缺口（手动触发 `sync("okx_perp_daily")` 或等 10-10 晨档并验证），根因若在调度器/zombie-reset 则修。
2. **OKX 盘中粒度全铺（2026-10-20 用户裁定；v3 按同判补齐配套）**：
   - **数据行（迁移 0159，0134 范式：sync_config+sync_kind_config 双表各三行）**：okx_perp_{hourly,1min,15min}——sync_mode=incremental、schedule=manual、enabled=true、trade_day_filter=none、provider=okx、supports_backfill=true、**retention=2026-09-29（hourly）/2026-10-05（1min、15min）**。⚠ **start_floor 是死列（0139 后引擎零读，engine.py:226 SELECT 只取 retention）——0134 的列写法不可照抄**；kind_config 三行 kind=bar_minute、sub_kind=perp、pg_table=bar_1h/bar_1min/bar_15min、pk_cols={symbol,ts}。
   - **注册与守门五件**：① engine _OKX_BAR_SPECS 三规格 + _make_crypto_bar_handler 实例化 + _HANDLERS 落位；② quant_common/markets.py SYNC_ID_CAP_MAP 三键→hist_quote（缺则 test_batch99 红）；③ okx_adapter capabilities 三 sync_id + capability_decls 补 (bar_minute, historical)；④ engine _RECONCILE_SYMBOL_SCOPE 三行（缺则对账静默跳过；⚠ _GAP_ROW_BUDGET bar_minute=50k 上界对 1min 档 70 万行/轮 会 budget_exceeded——binance 1min 同病，记不修）；⑤ 连带闸门行数断言更新（test_sync_config_coverage 30→33、handler 24→27、test_batch99 可回补集 21→24）。
   - **调度压力（v3 按档分列）**：15min 约50s/轮；hourly（7 天窗×2 页）约97s/轮；**1min（1440 根/天 ÷100/页=15 页 ×485）约12min/轮**——三项与 okx_perp_daily 共享同一 AWS relay 出口（单点、20req/2s IP 级共享），启用节奏＝relay 带宽实测+存储量级评估后**用户裁定** cron 化与 retention 放宽（0134 同构出口；manual 期间无并发面，**OKX 任务须串行触发**——实例级限频窗不共享，并发即合并超打出口 IP）。

3. **时间轴表达重裁（v2 已裁定「维持现状」，待落档）**：`bar_daily` kind + `trade_day_filter=none` 表达 crypto 连续自然日语义。**判据**：① `historical` 语义＝「逐日权威批量」，与 crypto 日线逐日落库的消费形态一致（回测/因子按日重放不区分交易日/自然日）；② `streaming` 是流式推送语义，re-label 违反 kind→时态映射硬闸（`contract.py:56`：bar_daily 仅 historical，re-label 即 is_legal=False）；③ 扩枚举（如 `continuous`）无消费者——零读点的类型词=第二真源；④ 实证（v3 修正）：binance 段 913/914 标的内部无逐日断裂（天数分布 min=14/中位=19/max=1987——多为近期上架，非同步断裂）；OKX 段连续性留 prod 佐证（dev 出不了网恒 0 行）。② 硬闸：contract.py:56 KIND_TEMPORALITY[bar_daily] 仅 historical——re-label streaming 直接 is_legal=False。**零代码**，决策落 `flow/方案/多市场数据接入与代理体系-设计-20261006.md` §二 + `flow/decisions.md` 决策 B 追加闭合行（追加制），引用回本文件。
4. OKX 重试预算（v3 修正：**仅改一处**）：okx_adapter.py:64 _MAX_RETRY = 3 → **5**（2/4/8/16/32s）。网络异常**无需扩集**——:172 except requests.RequestException 已全捕（ConnectionError/Reset/Proxy/Timeout 皆子类）且退避；102b 的 7/485 是预算不够非未重试。**连带**：test_batch102b 重试面断言同步更新（1+3 形态→1+5、退避 2/4/8→2/4/8/16/32）。二轮补拉失败标的落 _PARTIAL 冻结由日线档下轮兜底（既有机制，非本批改动）。

5. **调度异步派发（v3 并入，D5）**：tasks.py:730 _SYNC_ASYNC_DISPATCH 补 okx_perp_daily: 3600 + binance_perp_daily: 3600 两键（先例 pool_data；sync_via_celery soft_time_limit=3600 已具承接力）——消 135~1660s 调度轮内联阻塞（dev sync_log 实证；300s 软限下无保护）。盘中档 manual 不进 beat，cron 化时再加。

## 限定范围（v3 补齐五件后全集）
改：okx_adapter.py（bar 拉取+capabilities/decls+重试）、engine.py（SPECs+工厂+_HANDLERS+_RECONCILE_SYMBOL_SCOPE）、quant_common/markets.py（CAP_MAP 三键）、tasks.py（异步派发两键）、迁移 0159（双表三行）、tests/（101b 同款六组新文件+102b 重试断言+三闸门行数）、设计文档 §二+decisions（产出 3 落档）。
**不碰**：币安侧已稳定路径、批 109 sync_gap 机制、A 股同步、_GAP_ROW_BUDGET 上界（记不修）、provider 级锁（默认不加——manual 无并发面，操作约束代偿）。

## 接口契约（v3 全量）
- **bar 词映射（大小写敏感，OKX 拒 1h 形态）**：内部 freq 1h→1H、1min→1m、15min→15m（日线先例 1Dutc——盘中档是否需 utc 变体＝待 prod 实测裁：小时/分钟界天然 UTC 无歧义，倾向不加 utc，实测确认）；非内部 freq 响亮拒绝（ValueError，101b 同钉）。
- **handler**：_make_crypto_bar_handler("okx_perp_hourly","bar_minute","perp","1h") ×3（freeze_on_partial 不带——分钟/小时档 G-S1 惯例）；okx_adapter.pull_minute(inst_id, freq, start_ms, end_ms) -> list[BarRow]（复用 candles，_LIMIT=100 分页）。
- **sync_config 三行**（列值见产出 2）；**sync_kind_config 三行**（kind=bar_minute/sub_kind=perp/pk_cols={symbol,ts}）。
- **读写表**：写 bar_1h/bar_1min/bar_15min；读 sync_config/sync_kind_config/security_master；记 sync_log/sync_gap。
- **异步派发**：_SYNC_ASYNC_DISPATCH += {"okx_perp_daily": 3600, "binance_perp_daily": 3600}。

## 验收标准（v3）
1. 迁移 0159 往返绿（test-migration-roundtrip.sh 含 case_ag）；dev upgrade 后 sync_config/sync_kind_config 各 +3 行、**retention 三值非空**（NULL=实施错列即红）
2. cd server && venv/bin/python -m pytest -q 全绿（含 test_batch99 CAP_MAP 覆盖、三闸门行数断言更新值、102b 重试断言 1+5/2·4·8·16·32）
3. OKX 盘中新测六组（101b 范式）：interval 映射严格性（含非内部 freq 拒绝）/三注册表一致/窗口与首跑地板（retention 路径）/工厂游标契约（cursor_upto/skipped/no_rows/partial）/真库对账三面钉/限频共享声明
4. bar 词映射单测：1h→1H 等三行+utc 变体占位断言（待 prod 实测前以常量表钉）
5. 异步派发：data_sync_scheduler 触发 okx_perp_daily 走 celery 派发（调度轮秒回；dev 出不了网即 error 也是派发后的 worker 侧 error）
6. 时间轴：设计文档 §二+decisions 落档，零代码
7. 真机尾段（prod，上产后）：手动串行触发三档各一轮——15min 约50s / hourly 约97s / 1min 约12min 量级对照；bar 数据 UTC 界与 1Dutc 对齐核

## mock 方式

同 101b/102b（adapter mock 返回可辨识假数据；不连真 OKX——dev 出不了网，真机留 prod 实测）。补：bar 词参数断言（mock 捕获请求 params[bar] 比对映射表）。
