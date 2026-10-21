# 批 133 · 阶段 8 file 分支 wrapper 化（C 方案：修好备着）

> 立项 2026-10-21（用户裁 C）。**状态：立项挂待办，暂不编码**（真机 redis 模式下该分支永不执行，优先级最低）。

## 背景

release.yml 阶段 8 的 hub 心跳验证有两分支：redis（staging/prod 实际配置，quant-hbcheck wrapper 合规）与 file（**仅沙箱配置**，deploy 同用户直读 0700 json——沙箱自然可行，真机必 EACCES）。且 `hub_hb.json` 在 server/src 无写者——file 模式是沙箱专属测试形态，真机切 file 需「hub 加 json 写入 + wrapper 装位 + 配置切换」的组合工作，不是开关。

## C 方案产出（待开工）

1. `deploy/wrappers/quant-hbfile` 模板进 repo（quant 身份读 `hub_hb.json` 验 gen+8 字段+新鲜度，形态仿 quant-hbcheck；**不装位**——装位=michael 通道）。
2. release.yml file 分支改调：沙箱（`deploy_sudo_quant=""`）直接跑仓库 wrapper=同用户直读行为不变；真机（装位后）`sudo -u quant` 形态=修好备着。
3. 任务文件写明切 file 模式前置：装位 + hub 侧 json 写入确认。
4. 沙箱回归一次彩排（file 路径仍绿）。

## 挂账状态
不编码。复活条件=真机切 file 模式（届时装位+本批编码）。
