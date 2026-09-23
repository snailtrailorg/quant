# 多账号源 D6 · 加密 per-venue hub 部署与自愈（详细设计）

> 上级：`docs/architecture/31-多账号源架构设计.md` §八 D6。状态：详细设计（待双盲审）。依赖：D3（流键/租约键）。

## 一、目标

加密 per-venue hub 是全新部署拓扑（N 实例，每 venue 一个 MD+TD 源），钉死实例生命周期、SA4 期望态、租约/维护键分 venue、停用反拉起。

## 二、现状（已核实）

- A股 MD 单 hub 用 doc14 M5「单活双实例」+ 批60 M5 协议（Valkey 四键 + guarded Lua 条件推进 + `active_instance` 重启仲裁）。
- 加密 per-venue 后是 N 个 hub 实例（每 venue 一源），M5 协议的键需加 venue 维度（对齐 D3 流键 `{venue_id}`）。

## 三、设计决策

- **无首决**。复用批60 M5 协议骨架，扩展 venue 维度；本件只钉「N 实例生命周期 + 键分 venue + 反拉起」。

## 四、契约

- **实例生命周期**：加密 hub 按 venue 独立实例（N 个），`active_instance` 仲裁按 venue 分键。
- **SA4 期望态按 venue**：期望实例集合 = enabled 且 capabilities∋trading 的加密 venue。
- **租约/维护键分 venue**：对齐 D3（`quant:hb:md-hub:{venue_id}` / `hub:lease:{venue_id}`）。
- **停用反拉起**：停用某 venue 时先设维护键、后停实例，防 SA4 300s 反拉起；新增 venue 先注册 DB 后 systemctl。

## 五、限定范围

只做：加密 per-venue hub 的 N 实例生命周期 + SA4 期望态 + 键分 venue + 停用/新增流程。
不碰：D1/D2/D4/D5、A股单 hub 现有 M5（不改，加密只复制骨架加 venue 维度）。

## 六、验收标准（编码阶段定，行为级）

- 新增/停用一个加密 venue，对应 hub 实例正确起/停，不被 SA4 反拉起。
- 某 venue hub 掉线，仅该 venue worker 受影响（另一 venue 正常收行情）。
- 租约/心跳键按 venue 隔离，无跨 venue 串键。

## 七、参考文档

1. `docs/architecture/31-多账号源架构设计.md` §八 D6
2. `docs/architecture/14-设计.md`（M5 单活双实例）+ `docs/任务/批60-方案集.md`（Valkey 四键 + guarded Lua + active_instance）
