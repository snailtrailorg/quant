# P2-2 · dev 脚本 sleep-1 改条件等待（等待原语审计顺手件）

> 立项：2026-08-27 深夜等待原语审计。步 2 双盲修订：wait_pg 条件从 pg_isready 改为
> **按站点真条件轮询 psql**（pg_isready 测"PG 活着"恒首拍即真——等的是 reload 后 hba 规则生效）。

- **目标**：本地 dev 脚本 4 处固定 sleep 改为"真条件轮询+有界超时"。
- **依赖（就绪）**：无（纯 shell）。
- **产出**：
  1. `scripts/dev-init-valkey.sh:12`：`sleep 1; ping` → ping 轮询 10×0.5s（valkey-cli 与 redis-cli 双探测**置于 if 条件内**，PONG 即出；超时 exit 1 带手动提示）
  2. `scripts/dev-init-db.sh`：两处 `reload_pg; sleep 1` 改**按站点真条件**——①:24 后轮询 `psql -U postgres -d postgres -c 'select 1'`（trust 生效才成功，10×0.5s）；②:52 后轮询 `psql -U quant -d quant -c 'select 1'`，轮询成功即 :55 验证结论（echo 沿用）
  3. `scripts/dev-start.sh` `stop_all()`：kill 后 `kill -0` 轮询（20×0.25s；**探测置于 while 条件**——`set -euo pipefail` 下 kill -0 非零会直炸；bp/fp 为空跳过）；超时后 `yellow` 提示进程未退（best-effort 继续，不引入 kill -9 新行为）；`restart)` 分支（:172）冗余 sleep 删除
- **限定范围**：只动 3 脚本上述 4 点；**不动** dev-start.sh:94/113（已是健康条件轮询）。
- **接口契约**：shell 行内改动+`wait_*` 轮询块，无外部调用方。
- **验收标准**：
  - `bash -n` 三脚本零输出
  - valkey 轮询路径实跑：前置 `sudo systemctl stop valkey` 再 `bash scripts/dev-init-valkey.sh` → ✓ PONG（走新轮询分支）
  - 超时路径：`bash -c 'source <(sed s/10×0.5s/2×0.1s/ …)'` 或手动验证（过/未验分列：超时分支未验注明）
  - dev-init-db.sh 仅语法验证（需 sudo 动 hba，**注明"未验"**）
  - restart 分支由 bash -n+代码审覆盖（注明）
- **mock 方式**：N/A（shell）
- **参考文档**：`flow/规范/八步法.md` 守则节 原则 1/3（等真条件；读状态不猜状态）
