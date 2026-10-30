# 批 142 · alert_history 僵尸表清理（两段式：A 声明 → B contract）

> 立项 2026-10-23（用户裁定，选项 2：直接小批立项）。收口批 129 挂账（见 docs/obsolete/任务归档/批129-数据库垃圾清理.md 现状#5）。
> 表事实（批 129 已实证，本批复核仍成立）：0027 建表、0 行、全仓 src/tests 零引用（仅 init-schema.sql:219-229 手工参考档 + schema_expectations.txt:5）。

## 目标
DROP public.alert_history；A 段先行为 contract 铺 pair 合规通道。

## 结构性两段式（门规则推演，均已实证）
evaluate() 的 pair 校验锚 **prev（上一已部署版）**（migration_policy.py:345-353）⇒ 0027 的 expand 声明必须先随一版上产，0161 contract 才能合法配对；同发布捆绑已被负控实证拒绝（rc=1「pair=0027 在上一已部署版存在但未声明 phase=expand」）。0027 的 upgrade 段只有 create_table（drop 全在 downgrade）⇒ scan_unsafe_lines 零命中，加声明行即绿（探针 rc=0 实证 2026-10-23）。

## A 段（已编码，本批上产）
0027 文件第 1 行加声明行（phase=expand + 回溯标记注释）——schema 零变化、upgrade 段零变化（声明行在 upgrade 段之外，门 by design 容忍，见 migration_policy.py:394）。
验收：门探针 rc=0（受管恰为 {0027}）✓ 已过；门单测 104 passed ✓。

## B 段（**A 上产后**才编码提交——0161 提前入仓会挡住 A 的发布）
1. server/migrations/versions/0161_drop_alert_history.py：头声明 phase=contract pair=0027；upgrade=op.drop_table("alert_history")；downgrade=按 0027 形态重建六列（id/ts/level/title/body/channel）——语义=结构可回滚不 resurrect 数据（照 0158 口径写 docstring）。
2. server/src/data_platform/schema_expectations.txt：生成式重跑（命令见 db.py:388-394，禁手写）。
3. server/scripts/init-schema.sql：删「13. alert_history」节（CREATE+OWNER）。
4. scripts/test-migration-roundtrip.sh 新增 case_ai：FROM_AI=0160/TO_AI=0161；fixture=0027 形态 alert_history+样本行；断言=upgrade 后表消失 / downgrade 后表回且六列列集不变 / 数据不复活（0 行）/ 复 upgrade 幂等 / 离线渲染 up 含 DROP TABLE、down 含 CREATE TABLE。

## 限定范围（B 段 4 文件，声明超 ≤3 红线）
超红线依据：expectations/双档不同车即残留（批 129 同判先例）；case 不同车则破坏性迁移无往返回归（批 85 立法）。**明确不碰**：防误杀清单全部表、GC 策略、代码逻辑（src 零读点，无行为变更）。

## 引用
- flow/规范/破坏性迁移-两步走.md §4-§5 —— 两步节奏与硬规则
- flow/稳定性检查/批111门-起源与书签档案.md —— contract 型迁移上产前书签回顾（红线②）

## 接口契约
- 迁移：0161.upgrade() -> None（DROP TABLE）、0161.downgrade() -> None（CREATE TABLE 六列）。
- 读写表：alert_history（仅 DDL，零 DML）。

## 回滚语义
回滚链不退 schema；pre-0161 旧树零读 alert_history ⇒ 行为面兼容。旧树 expectations 含 alert_history、库已删表 ⇒ rollback 独立路径 SCHEMA_CODE_SPLIT 预期红（照 0158 口径，on-call 判读）。

## 验收标准（B 段）
1. bash scripts/test-migration-roundtrip.sh 全绿（含 case_ai）
2. 门探针 rc=0，受管集恰为 {0161}
3. dev 库 alembic upgrade head 后 information_schema.tables 无 alert_history
4. expectations 重跑后 diff 仅 alert_history 行
5. grep -rn alert_history server/src 零命中；init-schema.sql 零命中
6. 全量 pytest 绿

## mock 方式
无——纯 DDL 迁移；case_ai 往返回归即验证载体。
