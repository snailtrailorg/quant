# 批 133 · 阶段 8 file 分支 wrapper 化（C 方案：修好备着）· 已上产

> 立项 2026-10-21（用户裁 C）。**2026-10-23 编码完成**：随下一窗口（午休 11:36 已定）上产。

## 落地

1. **deploy/wrappers/quant-hbfile** 模板进 repo（+x；逻辑与原 release.yml 内联段**逐字等价**：age≤10s + gen+8 字段，缺失即非零）。
   - 头注写明真机切 file 模式前置：①michael 装位（/usr/local/sbin + chown quant:quant 755 + sudoers 行）②hub 侧 json 写入确认（server/src 无写者）。
2. **release.yml file 分支改调 wrapper**：`quant_hbfile_cmd | default(...)`——沙箱（deploy_sudo_quant=""）经 sandbox.yml 的 quant_hbfile_cmd 直跑仓库副本=同用户直读，**行为零漂移**；真机=装位后 sudo 形态（未装位时该分支必失败=切换 file 模式的前置闸）。
3. **sandbox.yml**：新增 quant_hbfile_cmd（同其余 wrapper 的拼接形态，串 deploy_hub_hb_file 入参）。
4. 部署链闸门 64 例全过（blast radius / no-ddl / input-consumer map / dnf wrapper）。
5. **wrapper 本体三态直验**：新鲜+字段齐=PASS；缺字段/过期 30s/文件缺失=各自红（rc≠0）。

## 沙箱彩排的诚实结论

整轮沙箱 release **未走绿**——失败在阶段 2/3，属**既有债**（非本批引入）：
1. 阶段 2：白名单切片含 requirements-dev.txt/deps 而 fixtures 基线没有 ⇒ rsync code 23（批 136 加切片时的漏网，**已顺手补占位**进 make_sandbox_root.sh）。
2. 阶段 3：importsmoke 第七入口（批 127 的 src.strategy_framework.emd.emd_quote_api）在 fixtures 最小 src 面 + pins venv 下必然 ModuleNotFound ⇒ **沙箱整套复验在修复前不可用**。

⇒ 已记新待办「沙箱 fixtures 基线滞后」（与批 111「沙箱 venv 重建」书签同域）；本批的行为验证改由 wrapper 三态直验 + 64 例部署闸承担。

## 挂账（不变）

- wrapper 未装位（装位只能人做）；复活条件=真机切 file 模式（届时：装位 + hub 侧 json 写入 + sudoers 行）。