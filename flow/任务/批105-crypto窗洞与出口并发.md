# 批 105 · crypto 窗洞与出口并发（partial 冻结游标 + 二轮补拉 + okx 会话复用）

## 目标

102b 上产自曝的两条同族债，一个 release 收掉：

1. **窗洞（数据正确性）**：OKX 首跑 **7/485 标的**（`EDGE/FIL/ICP/LGELECTRONICS/MEGA/UP/UVXY-USDT-SWAP`）adapter 3 次退避耗尽仍 `Connection reset`，而 `_make_crypto_bar_handler` 的游标**照推**到「成功标的的最大数据日」⇒ 那 7 标的的**首跑 30 天窗永久缺失**（下轮增量只补 1 天，补齐须人工 backfill）。
2. **出口并发（根因治理）**：批 104 真机复验定性——`Connection reset` 是**与 485 标的同步共用同一代理出口时的并发压力**（连接表/限流），非纯随机瞬态。probe 侧已由批 104 补重试；本批修 **adapter 侧**：连接复用 + 抖动退避。

**威廉姆裁定（2026-10-06）**：「你确定顺序，反正都要做」——执行顺序批 105 → 106 → 107 由助手拍板；本批两条冻结/推进语义争议点一并由助手拍板（见下）。

## 现状（事实清单）

- handler 失败按标的记入 `failed_dates`（**名字叫 dates，实为 `sym:Err:msg` 串**——crypto 无逐日失败概念，一次 pull 覆盖整窗）；游标＝`reached`（成功标的最大数据日），与失败无关 ⇒ partial 失败不拦游标。
- **频率结构决定冻结代价**：日线档 cron **日频**（binance `30 8 * * *` / okx `40 8 * * *`）⇒ 冻结游标最多一天一重试，**无风暴**；分钟/小时档 beat 高频 ⇒ 冻结＝每拍整窗重拉（G-S1 立法动机，2026-10-06 批 101b）。⇒ **冻结语义可以按档位给，不必一刀切**（本批裁定：日线档冻结、分钟/小时档维持无条件推进）。
- **ratchet 风险（冻结的另一面）**：若某标的**永久**失败（下架/停牌态漏过滤），“失败⇒冻结⇒下轮窗更大⇒再失败”会让重拉窗**无界增大** ⇒ 冻结必须带下限。
- okx adapter **每请求新建连接**（裸 `requests.get`）⇒ 485 标的 × 多页分页 = 数千次 TCP+TLS(+SOCKS) 握手全压同一代理出口。
- 重试退避**无抖动**（全体标的按 2/4/8s 同节奏重试）⇒ 重试流量自己排成同步波峰，正好再撞出口。

## 产出（实现）

1. **`engine.py::_make_crypto_bar_handler(..., freeze_on_partial=False)`**：
   - **二轮补拉**：首轮失败标的**立即**各重试一次（不 sleep——瞬时抖动大多秒级自愈，sleep 会把全任务时长乘进去；adapter 内部已有 2/4/8s 退避兜间隔）。成功即清账。
   - **partial 冻结**：二轮后仍有残留失败且 `freeze_on_partial=True` 且非 backfill ⇒ 游标退回 `max(start, end - _PARTIAL_FREEZE_MAX_DAYS) - 1d`（`_PARTIAL_FREEZE_MAX_DAYS = 10`，**防 ratchet**：单轮重拉有界 ≈11 天 × 全标的；更老的洞靠人工 backfill，告警已响）。游标仍**单调不回退**（冻结点 ≥ 旧游标：短窗时＝旧游标值，长窗时＝end-11d > start-1）。
   - **分钟/小时档不冻结**（`freeze_on_partial=False` 缺省）：无条件推进，G-S1 原样——宁可窗口洞靠告警暴露。
   - **backfill 永不冻结**（sync() 对回补本就不推游标）。
   - 接线：`binance_perp_daily` / `okx_perp_daily` 两条日线 `freeze_on_partial=True`；hourly/1min/15min 缺省 False。
2. **`okx_adapter.py`**：`requests.Session`（HTTPAdapter pool 2/4，实例级、任务生命周期内连接复用）+ `_backoff(attempt)` 指数退避×(1+U(0, 0.3)) 抖动；**`Retry-After` 保持服务端原值**（尊重上游指示，不加抖动）。
3. **测试**：新 `test_batch105_crypto_partial.py`（handler 9 例 + adapter 会话/抖动 5 例）；101b 两用例按新契约改写（瞬时异常→自愈清账；持久失败→分钟档仍记账+推进）；102b 9 处桩 `OA.requests.get` → `ad._session.get`。

## 接口契约（不改面）

- handler 返回体**形状不变**：`failed_dates` 仍是 `sym:Err:msg` 串列表（含 `no_rows:...`），`cursor_upto`/`log_status`/`pulled`/`saved` 语义不变 ⇒ `sync()`、`_alert_sync_failure`、`sync_log` 落库、前端展示**零改动**（批 104 的终态词修源头原样受益：冻结轮的 `last_status` 将如实显示 `partial`，告警标题「缺口将自动重试」对日线档**从假话变成真话**）。
- `_get_supply_adapter` / `fetch_supply` / `to_bar_rows` 契约不变。

## 验收标准

- 相关面 pytest 绿；**全量 2191 → 2206**（+15 新例，基线已回填 skill）；
- 冻结（日线档）/ 不冻结（分钟档）/ ratchet 下限 / 回补不冻结 / 二轮自愈 / 二轮仍失败 / 全失败 / 排序输出 各有行为钉；
- 会话复用钉（模块级 `requests.get` 一用即红）+ 抖动上界钉 + `Retry-After` 不抖动钉；
- `ruff` 清（改动文件显式传路径）。

## 反向自证（mutation）

- 冻结分支置恒 False ⇒ `test_partial_failure_freezes_cursor_at_window_start` / `test_freeze_floor_caps_window_growth` 红；
- 二轮补拉块注释 ⇒ `test_second_pass_heals_transient` / 101b `test_per_symbol_exception_does_not_abort` 红。

## mock 方式

handler 桩：`patch.object(engine, "_get_supply_adapter")` + `patch("src.data_platform.db.save_bars")`；窗口用**过去日期**（`end_date=20240131`）避开「UTC 昨日」随真实时钟漂移（同批 101 惯例；勿 patch datetime——croniter 收 MagicMock 会假红）。adapter 桩：`patch.object(ad._session, "get")`。

## ⛔ 本批不含

- **交易窗门接 `trade_cal`** → 批 106（通道：控制器侧 `delegate_to: localhost` 查 dev 库 + fail-open，待办行已并）。
- **OKX instruments 按 `state` 收窄**：证据不足不预收窄（102b 立场不变）；若日后出现「永久坏标的」令冻结反复触发，这是第一候选。
- **binance adapter（S3/CDN 路径）会话化**：未观测到该路径失败；列为候选。
- **7 标的缺窗的数据回补**：代码外动作，上产后对 `okx_perp_daily` 触发一次 `backfill_from=20260905`（overwrite 全市场重拉 ≈15-20min），见交付状态。

## 交付状态（上产 2026-10-06）

| 项 | 结果 |
|---|---|
| 代码提交 | `70b623a`（已 push `origin/main`） |
| 全量测试 | **2206 passed / 1 skipped**（基线 2191 → +15）；ruff 清 |
| 前端门 | `npm run build` 绿（prebuild 令牌门 + 词条门） |
| 反向自证 | mutation 两发：冻结分支置恒 False ⇒ 3 红；二轮补拉短路 ⇒ 2 红；**均还原复绿** |
| staging 彩排 | `ok=76 changed=19 failed=0 unreachable=0 rescued=0` |
| prod 发布 | release **`202610062021-70b623a`**，`ok=81 changed=20 failed=0 unreachable=0 rescued=0`（20:21 交易窗外，无需 force） |
| 五证 | ✅ ① 双链 `server`/`web` 同指 `releases/202610062021-70b623a` ② `healthz`/`readyz`=200 ③ 迁移树 0135/0136/0137（本批无新迁移）④ 新码指纹 `engine.py` 9 处 `_PARTIAL_FREEZE_MAX_DAYS\|freeze_on_partial`、`okx_adapter.py` 4 处 `_session`、`proxy.py` 2 处 `PROBE_RETRY_ATTEMPTS`（批 104 保留）⑤ **probe 真机复验：连打 3 次全 `ok:true`**（六检含 `hub_hb in_set=4 fresh`） |
| 发布前保护 | 近 30min prod 零同步活动（`running` 计数 0）⇒ celery 重启无 SIGKILL 长任务 |

### ⏳ 遗留的人工动作（数据回补，代码外）

**102b 首跑的 7 标的 30 天历史窗不会自愈，须人工回补一次**。理由（已核代码）：

- `_crypto_window` 是**游标驱动**——`start = last_sync_date + 1天`；`first_run_days=30` **仅作用于首跑**（无游标时）。故 102b 首跑后游标推进，下一轮起点＝游标+1 ⇒ 那 7 标的在首跑窗内的缺失日**永不重取**（「下轮只补 1 天」）。
- 批 105 的 partial 冻结把重拉窗**卡在下限 10 天**（`_PARTIAL_FREEZE_MAX_DAYS`，防 ratchet），只能兜住「窗口最后 10 天」——**更老的洞超出冻结下限，必须 backfill**。

**动作**：prod 无任意 SQL/Python 通道（刻意边界）⇒ 由操作者在 **DataManage 回补入口**对 `okx_perp_daily` 触发 `backfill_from=20260905`（overwrite 覆盖式重拉，全 485 标的 ≈15-20min），补齐 7 标的（`EDGE/FIL/ICP/LGELECTRONICS/MEGA/UP/UVXY-USDT-SWAP`）首跑窗缺日。批 105 上线后，**同类未来窗口洞**由冻结机制自动在 10 天界内自愈，不再需要人工。
