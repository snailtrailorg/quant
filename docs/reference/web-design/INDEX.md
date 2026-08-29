# web-design · 设计交付物（2026-08-29 定稿）

> 来源：~/Projects/web-design（独立设计仓库，本目录为其快照）。实施按 **11-实施交接.md** 为入口。
> 可运行原型（未拷入本仓库，避免 git 膨胀）：`~/Projects/web-design/prototype/SnailQuant-Full.html`（双击打开，hash 直达视图，索引见 11 号卡文末）。

## 阅读顺序（施工最小集）

1. **11-实施交接.md**——施工图：通则 5 条 / Phase 0-4 任务卡（文件方向·依据·验收）/ 勿误修 11 条
2. 按需查阅：04-设计系统（令牌/色彩预算/字体阶梯）· 05-关键页面重设计（各页交互定义）· 10-权限体系（多维模型与现状差距）
3. 背景与依据：01-体验审计（问题清单）· 03-信息架构 v2.1（菜单/权限矩阵）· 06-功能差距对照（含 08-28 上产校准注记）· 09-三途径遗漏处置 · 08-双盲仲裁两轮 · 07-路线图 · 02-角色旅程

## 关键定案速览

- 品牌：中文**蜗牛量化交易** / 英文 **SnailQuant Trading**（短版 SnailQuant）
- 涨跌色：A股红涨绿跌+▲▼双编码；crypto 页保持国际色；四令牌四色相（up/down/success/critical 分离）
- 菜单 v2.1：策略研究/实盘交易/风险控制/系统管理 四组 16 项；组标题与菜单项同字号
- 熔断=轻确认、恢复=输入确认；策略无启停是设计、symbol:"" 是契约（勿修）
- 权限：三维（菜单/API/数据）×两级（角色+单用户覆盖），个人中心入口钉死顶栏

## 原型视图

`SnailQuant-Full.html#page=overview|risk|reconcile|livetask|backtest[&view=report]|strategy[&view=edit[&mode=py]]|screener|pool|factors|daily|trading|riskrules|data[&tab=…]|integrations[&tab=…]|observe[&tab=…]|settings[&tab=…|perm]`
