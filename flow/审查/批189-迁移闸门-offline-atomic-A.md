# 批189 迁移闸门 offline-atomic 通道 · 盲审 A（治理改动）

基线 b189-m5-wip@5b2aa06。范围：migration_policy.py / 0192 声明 / 守门测试。P0/P1 only。

## P0

**P0-1 通道前提（服务已停）在部署路径上零强制、零留痕，门却打绿「合规」。**
`migration_policy.py:331-348` 以 reason/backup/restore 三串返回 rc=0 并打印「离线维护窗原子迁移」；但
`release.yml:516` 阶段 5 迁移实跑前**无任何停服/备份步骤**（`quant-alembic-wrapper` 内 grep 无 pg_dump/stop），
A06:81 亦明写「迁移由阶段 5 自动执行」。⇒ 迁移实际在 web/celery/hub/exec 运行时施加，正是本闸门要防的
「旧代码读新 schema」窗；「离线」只是作者自述。
修：阶段 4 增前置断言（`-e offline_window=true` + 阶段 4 校验备份产物存在/停服标记），不满足则维持 rc=1。

**P0-2 rc=0 让 0192 的「不可自动回滚」对管道隐形，rescue(6-8) 仍会翻旧代码上新 schema。**
`release.yml:486` 置 `auto_rollback_disabled` 的判据是 `rc==1 and allow_contract`；offline-atomic 返 0 ⇒ 不置位 ⇒
阶段 6-8 任一失败即自动回滚代码到 0192 之上（0192 downgrade 直接 raise）⇒ 必然 SCHEMA_CODE_SPLIT（批84/111 立法禁止的混合态），
与 A06:81「不支持自动回滚」冲突；且 A06 文档要求的 `-e allow_contract` 在此通道下安全半边静默失效。
修：offline-atomic 返独立码（如 4），阶段 4 判据纳入 4 并置 `auto_rollback_disabled=true`（同步改 `test_release_playbook_has_no_ddl_logic.py` + A06 表）。

## P1

1. **P1-1 attestation 是 3 token 的荣誉制**（`migration_policy.py:335-347`）：`reason="x" backup="x" restore=rehearsed` 即 rc=0，无名单、无产物校验、无长度下限；比批111 P0-1 修掉的 LEGACY_FROZEN 软绕过更弱（后者至少名字硬编码+逐字比对）。
   修：加 `OFFLINE_ATOMIC_APPROVED: frozenset[str]`（名 keyed，同 LEGACY_FROZEN 纪律），名不在集内一律拒。
2. **互斥校验位置错**（`:321` 早返 vs `:335` 互斥）：无命中文件写 `phase=offline-atomic pair=0122` 静默通过，与模块自述「legacy 与 phase/pair 互斥」不一致（legacy 互斥在 `:306` 是命中前判的）。
   修：把 pair/legacy 互斥移到 `if not hits` 之前。
3. **文档双真源**：模块 docstring `:2`「两步走（expand → contract）唯一判定实现」、`:271` 返回 4 键、`:353`「唯一合规声明＝contract」均已假；`flow/规范/破坏性迁移-两步走.md:51-53` 与 A06:628 仍只列 expand/contract/legacy。
   修：同批重写规范 + docstring（状态文档重写制），勿留第三通道无出处。
4. **守门测试只钉 2 条拒路**（`test_migration_policy.py:687-702`）：缺全量+带 pair 已覆盖，但 `restore=planned/true`、`reason=""`、`backup=""` 三个最可能的错法未钉；亦无「dummy 值可通过」的特性断言把绕过面写实。
   修：补 3 条断言（RE 现只认裸 token，`restore = rehearsed` 亦落空 ⇒ 顺手钉住）。

结论：**NEEDS-REWORK**（P0-1、P0-2 必闭合；P1-1/2 同批，P1-3/4 可随行）。
