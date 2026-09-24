# 多账号源 D6 · 加密 per-account hub 部署与自愈（详细设计）

> 上级：`docs/architecture/A05-多账号源架构设计.md` §八 D6。状态：详细设计（待双盲审）。依赖：D3（流键/租约键）。

## 一、目标

加密 per-account hub 是全新部署拓扑（N 实例，每 account 一个 MD 源；TD 仍在 worker 侧，归 D5），钉死实例生命周期、SA4 期望态、租约/维护键分 account、停用反拉起。

## 二、现状（已核实）

- A股 MD 单 hub 用 doc14 M5「单活双实例」+ 批60 M5 协议（Valkey 四键 + guarded Lua 条件推进 + `active_instance` 重启仲裁）。
- 加密 per-account 后是 N 个 hub 实例（每 account 一源），M5 协议的键需加 account 维度（对齐 D3 流键 `{account_id}`）。

## 三、设计决策

- **无首决**。复用批60 M5 协议骨架，扩展 account 维度；本件只钉「N 实例生命周期 + 键分 account + 反拉起」。

## 四、契约

- **实例生命周期**：加密 hub 按 account 独立实例（N 个），`active_instance` 仲裁按 account 分键。**分源键=account_id（=external_interface.id）；换凭证走 update 保 account_id，禁 delete+insert 重建（否则与 D3 流键/暖机/order_log 归因脱钩）**。
- **SA4 期望态按 account**：期望实例集合 = enabled 且 capabilities∋trading 的加密 account。
- **租约/维护键分 account**：对齐 D3（`quant:hb:md-hub:{account_id}` / `hub:lease:{account_id}`）。
- **停用反拉起**：停用某 account 时先设维护键、后停实例，防 SA4 300s 反拉起；新增 account 先注册 DB 后 systemctl。

> **编码实施补充（2026-09-24，AB 双盲审后）**
> - **ACCOUNT_ID 与 HUB_INTERFACE_ROW 拆开**（双盲审 B P0/P3）：`HUB_INTERFACE_ROW` 原语义=A股切换 B 实例选账号，D6 不能复用它做 account 分键（会污染 A股 A/B 切换）。新增独立 `ACCOUNT_ID`（systemd `Environment=ACCOUNT_ID=%i`，仅加密实例 %i=数字），main.py `account_id = int(ACCOUNT_ID) if ACCOUNT_ID.isdigit() else None`（A股实例名 "quant" 非数字 → None 键不变）。
> - **键 account 化**：`_key(base, account_id)`（account_id=None → base 零回归，否则 `base:{account_id}`），parts.py/main.py 的 lease/gen/active_instance/surrender/intent/hb/latest_tick 全经 _key。
> - **停非期望加密 hub 判定**：实例名解析纯数字 account_id 才是候选（防误停 A股切换目标 quant2）；不打维护键（反拉保护已由 desired 集合+租约提供，维护键 TTL 会挡重新启用）。
> - **加密 hub 期望加 provider 过滤**：`AND provider = ANY(list_md_gateway_providers())`——加密 MD 网关未接入的 provider 不进期望表，防「拉起→create_md_gateway ValueError→78→告警」死循环。
> - **挂账（加密接入批）**：BinanceMdGateway/OkxMdGateway 子类 + register_md_gateway、加密会话模型（24/7 vs A股时段）、bar 流键发布侧 account 化（`hub:bars:{account_id}:{symbol}` + account_id 字段 + 交易所映射）、加密 provider quote 能力声明。

## 五、限定范围

只做：加密 per-account hub 的 N 实例生命周期 + SA4 期望态 + 键分 account + 停用/新增流程。
不碰：D1/D2/D4/D5、A股单 hub 现有 M5（不改，加密只复制骨架加 account 维度）。

## 六、验收标准（编码阶段定，行为级）

- 新增/停用一个加密 account，对应 hub 实例正确起/停，不被 SA4 反拉起。
- 某 account hub 掉线，仅该 account worker 受影响（另一 account 正常收行情）。
- 租约/心跳键按 account 隔离，无跨 account 串键。

## 七、参考文档

1. `docs/architecture/A05-多账号源架构设计.md` §八 D6
2. `docs/design/D14-设计.md`（M5 单活双实例）+ `docs/obsolete/任务归档/批60-方案集.md`（Valkey 四键 + guarded Lua + active_instance）
