# 批 94 · clean-sync 演练 + trade_cal 坏导入修复

> 立项：2026-10-04 深夜。状态：实现+演练进行中。
> 背景：用户提案「清空所有数据和日志 → 干净同步一遍 → 挖洞 → 看能否回补」。
> 裁决：演练放 **staging（本机）**，prod 只做点状验证；重建起点=**复刻式**（每表按原最早日期）。

## 一、立项分析（为什么不是「清空生产」）

- 仓内**零清空路径**（`grep TRUNCATE` 只命中 vendored 库）⇒ 演练工具必须自建。
- **清日志=自毁取证**：prod 只有只读 `quant-journal`，清 journald 要 root 开交办单；
  且清完再同步，失败连证据都没有 ⇒ 用「T0 时间戳分段」代替清空。
- **tushare 全项目单账号**（token 在 `data_source` 表）⇒ staging 全量重拉烧共享配额；
  现为国庆假期，prod 下个同步日 10-09，无冲突窗口。
- `quant` 用户无 CREATEDB ⇒ 开不了隔离库，只能 staging 原地清空（先 pg_dump 兜底）。

## 二、契约

| 项 | 契约 |
|---|---|
| 工具 | `server/scripts/rehearse_clean_sync.py`：snapshot / backup / clear / resync / poke-cursor / poke-delete / repair / verify |
| 门禁 | 三道：目标库 host 必须本机；破坏性动作须 `--commit`；另须 `--yes-nonprod`。默认 dry-run |
| 清空集 | `CLEAR_TABLES` 29 张＝**只含同步引擎能重建的市场数据表**（实测市场表零外键）；**不碰**身份层（security_master/security_state）、业务表、所有日志 |
| 复刻式游标 | `clear --from auto`（默认）：TRUNCATE **前**读各表原最早日期，增量型 sync_id 游标=最早日期−1 天 ⇒ 重同步复刻相同或更好水位 |
| 重建顺序 | trade_cal → 身份/清单 → bar 族 → tier1 → pool_data（日历最先，b92 可用上界依赖它） |

## 三、顺带挖出的真缺陷（彩排的最大红利）

**`pull_trade_cal` 坏相对导入**：`tushare_adapter.py:360` `from .db import get_conn`——
adapters 包内**无 db.py**（正确 `..db`，同文件 :350/:439 均对）⇒ ModuleNotFoundError，
**`trade_cal` 同步自引入起一直静默失败**（staging 游标停在 20260725 即铁证），全量测试绿
——零用例覆盖。修复 + `tests/test_trade_cal_import_fix.py` 2 钉（回退即红已实测）。

## 四、演练结果（staging 实测）

- **演练 B（补洞）**：`poke-cursor margin_detail_sync→20260831` + repair ⇒
  `margin_detail` **8,888 行(2 天) → 93,352 行(08-31~09-29)**，19s。b92 游标语义端到端可用。
  残留 `09-30`=0 行系 `lag=1` 非交易日取 `rows[1]` 的保守行为，下一交易日自愈，**非缺陷**。
- **演练 A（清空→全量）**：pg_dump 370MB 兜底 → 清空 29 表 → 复刻游标复位 →
  resync + celery 调度双路重建。中途发现工具旧函数体未随编辑落盘（游标被写字面量
  `auto`）——教训：**长后台任务启动前必须回读编辑结果**。
- **数据完整度快照（重大发现）**：除 `bar_1d`(13.7M, 2010 起)、`bar_index`(2005 起)、
  `namechange`(截断 10000) 外**普遍极浅**——daily_basic 仅 07-20 起、tier1 仅 ~1 月、
  forecast 仅 3 行、trade_cal 仅 2026 一年；二档池表个位数到两百行。「执行面全绿」≠完整。

## 五、产出

- `server/scripts/rehearse_clean_sync.py`（新）
- `server/src/data_platform/adapters/tushare_adapter.py`（坏导入修复）
- `server/tests/test_trade_cal_import_fix.py`（新，2 钉）
