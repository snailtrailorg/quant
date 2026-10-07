# 批 84 · 回滚诚实性：让「自动回滚」不再掩盖 schema–代码撕裂

> ✅ **2026-10-07 威廉姆裁定（开工许可）**：探针选 **A**；开关语义 **（i）+（iii）**（（iii）根治在批 85，本条只做开关语义对齐）；**（ii）无条件做**（最低限度文案诚实，不得再输出「已自动回滚」掩盖 schema 未回退）。
> 判据（非成本）：**执行面闭合**——撕裂发生在「运行时读表」，B 只在启动期设防、防不住它要防的东西；A 的清单从**代码树导出**（非手写）⇒ 真源仍是代码，不制造第二真源。
> 关联：本条与 `批 85·b` 同根因（84＝管道诚实性，85＝迁移纪律），实施时**批内不展开 85 的 DDL 门立法**，只对齐开关语义。

> 立项 2026-09-30（威廉姆裁定「立」）。来源＝批 83a 拆表过渡期勘察（对 prod 只读实查）时发现：
> 拆表迁移（`DROP external_interface`）与「失败自动回滚」这张安全网**互相矛盾**，且回滚后的
> 复验**看不见**这个矛盾。本条**与 83a 无关**——83a 只是把它照出来的那束光。
> 关联立法：`flow/任务/批85-破坏性迁移两步走立法.md`（同一根因的两面：84＝管道诚实性，85＝迁移纪律）。

## 目标

管道任何回滚路径（release 的 rescue(6-8) 自动回滚、`rollback.yml` 手工回滚）都必须**校验「回滚后的
代码」与「当前 schema」的兼容性**；不兼容时**明确报「schema–代码撕裂·需人工」**，绝不输出
「发布失败已自动回滚」这种掩盖式结论。

## 现状（已实证，非推测）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | 回滚**不动 schema**：`rollback-tasks.yml` 全文无 alembic/downgrade | `deploy/playbooks/rollback-tasks.yml`（逐节已核） |
| 2 | 回滚复验只有两项：**版本收敛断言** + **`/healthz` 200** | `deploy/playbooks/rollback-tasks.yml:138`（quant-pinned 收敛）、`:163`（healthz） |
| 3 | `/healthz` 是 **liveness，注释明写「不查依赖」** | `server/src/web_api/routes/system.py:162-164` |
| 4 | `/readyz` 升级也**不够**：只查 PG/Valkey **可达性**，**表/列缺失时 PG 仍可达 → 200** | `server/src/web_api/routes/system.py:167-179` |
| 5 | rescue(6-8) 的自动回滚文案断言「schema 已前进不回滚——**expand-only 前提经 DDL 门保证**」 | `deploy/playbooks/release.yml:582`、`:585` |
| 6 | 而 DDL 门**只拦不修**，且 `allow_contract=true` 即放行；该标志只置 `auto_rollback_disabled: true`（`:349`），**仅被 rescue(2-5) 消费**（`:385`、`:392`）——**rescue(6-8) 不看它** | `deploy/playbooks/release.yml:315-350`、`:380`（rescue 2-5）、`:569`（rescue 6-8） |

**合起来＝一条静默失败路径**：contract 型迁移（DROP 旧表）→ 阶段 5 迁移成功 → 阶段 8 postverify 撞已删表
失败 → rescue(6-8) 回滚**代码**、**schema 留在新版** → 复验只断言「版本收敛 + /healthz 200」（两者**都过**）
→ 管道报「已自动回滚」→ 交给运维一台**跑着旧代码、而它依赖的表已被删除**的 prod。

> 注：`release.yml` 第 5 条那句「expand-only 前提经 DDL 门保证」——**这个「保证」目前在 DDL 门里
> 并不存在**（门只认 `allow_contract` 开关，不校验迁移是否真的 expand-only）。本条要治的就是这句
> 自我陈述与实现之间的落差。

## 依赖（就绪）

`deploy/playbooks/{release,rollback,rollback-tasks}.yml` ✅｜`quant-hbcheck`/`quant-dbro` psql 通道 ✅
（属主 quant、PG peer 免密）｜`health_monitor.collect` 依赖探测口径 ✅｜静态扫部署链的闸门先例 ✅
（`server/tests/test_deploy_blast_radius.py`，2026-09-30 已立）｜沙箱场景套件 ✅（`deploy/tests/run_scenarios.sh`）。

## 产出

1. **代码↔schema 兼容探针通道**（本批核心）—— ✅ **已裁定 A**：
   - ✅ **A（裁定）**：新增 `deploy/compat/<release_id>.json` 兼容清单——由 release 阶段从**待部署 release 树**
     导出该版代码的运行时表/列依赖（来源：`src/data_platform/schema_expectations.txt` 同源机制或新增
     导出脚本），装位为 `/usr/local/sbin/quant-hbcheck` 的兄弟动作 `quant-hbcheck compat`（复用其 psql 通道）；
     回滚后用它断言**回滚目标版的清单 ⊆ 当前库实际 schema**。
   - B（轻量但覆盖窄，**已否决**）：回滚目标版跑 `quant-importsmoke-wrapper` + `/readyz` 加「关键表存在性」——
     只能覆盖启动期，运行时读表仍可漏。
2. `deploy/playbooks/rollback-tasks.yml`：在「回滚复验」（`:138`/`:163`）之后**新增兼容复验任务**；失败 →
   输出固定词条 **`SCHEMA_CODE_SPLIT`（schema–代码撕裂）** + 非零退出 + 告警，**并明确声明「不建议前滚/回滚
   自动处置，须人工」**。
3. `deploy/playbooks/release.yml`：
   - ① rescue(6-8) 复用同一兼容复验（不得只在 rollback.yml 做）；
   - ② **`auto_rollback_disabled` 语义一致化**（✅ **已裁定 (i)+(iii)，且 (ii) 无条件做**）：
     (i) ✅ **裁定**：置该标志时 rescue(6-8) **也不自动回滚**——改为「停在已切换态 + 明确人工 runbook」，让「禁用自动回滚」名副其实；
     (ii) ✅ **裁定·无条件做**：把回滚文案里的「已自动回滚」一律改为「已回滚代码、**schema 未回退**」并打印实际 alembic 版本（最低限度诚实；**无论 (i) 是否落地都必须先做**）；
     (iii) ✅ **裁定·本批只做开关语义对齐**：DDL 门**拒绝** contract 型迁移（不再「给个 flag 就放行」）——**根治立法在批 85，本批不展开**。
4. 新增闸门 `server/tests/test_release_rollback_honesty.py`：静态断言（读 yml，`yaml.safe_load` + 结构断言）
   ① rescue(6-8) 与 `rollback.yml` **都**含兼容复验任务（防未来被删）；② `allow_contract` 与「自动回滚代码」
   不得共存而不声明；③ 回滚路径的失败词条必须含 `SCHEMA_CODE_SPLIT`。

## 限定范围

只改 `deploy/playbooks/{release,rollback,rollback-tasks}.yml`、`deploy/wrappers/`（新增/扩展探针动作）、
`deploy/compat/`（新增，若走 A）、`server/tests/test_release_rollback_honesty.py`（新增）。
**不碰**：`server/src/**` 业务代码、`server/migrations/**` 迁移链、`web/**`、`flow/`（除本文件与本批收工的进展/待办更新）。

## 接口契约

- 新增：`quant-hbcheck compat [--manifest <path>]` → stdout 逐行 `ok|miss <table>.<column>`；有 miss 则 rc≠0。
  （保持既有 `quant-hbcheck` 无参行为不变——兼容期不得破坏阶段 8。）
- 新增：`deploy/compat/<release_id>.json` 格式 `{"release_id": str, "tables": {"<tbl>": ["<col>", ...]}}`（仅存运行时依赖，非全 schema 快照）。
- 不改：`/healthz`（liveness 语义）、`/readyz`（可达性语义）——**本批不动服务端探针**，避免与批 85 交叉。

## 验收标准

- `pytest server/tests/test_release_rollback_honesty.py -q` → 全绿
- `cd deploy && .venv/bin/ansible-playbook playbooks/release.yml --syntax-check`（+ `rollback.yml`）→ 通过
- **行为级**：构造「回滚目标版清单 ⊄ 当前 schema」场景（scratch schema + 真 wrapper，同批复 83a 双态回归的手法），
  断言输出 `SCHEMA_CODE_SPLIT` 且 **rc≠0**；构造兼容场景断言 rc=0（**正反两例都要**）
- **反证钉**：撤掉兼容复验任务 → 闸门变红；恢复即绿
- 沙箱场景套件：六场景在**无 bulk-delete 限制**的环境跑（`deploy/tests/run_scenarios.sh`；本沙箱受 safe-delete 守卫限制，
  须按段复现并在报告中如实标注）
- 全套不回归：`pytest server/tests/ -q`

## mock 方式

- 闸门＝**静态文件断言**：`yaml.safe_load(release.yml)` 后遍历 block/task 名找兼容复验，不连环境。
- 行为级＝scratch schema（`CREATE SCHEMA` + 手建两版表形态）+ 真 wrapper（`PGHOST`/`PGOPTIONS` 驱动），
  与批 83a 双态回归同一套手法，**不连服务器**。

## 参考文档

1. `deploy/playbooks/release.yml`（阶段 4 门 `:315-350`；rescue 2-5 `:380`；rescue 6-8 `:569-590`）
2. `docs/obsolete/任务归档/批3-工件化交付.md`（工件化交付设计稿——回滚语义的原始出处）
