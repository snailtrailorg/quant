# 批189 迁移闸门 offline-atomic · 盲审 A 复审（同审，未读 B）

基线 37bd02d（前审 5b2aa06）。逐项复核 + 新查。

- **P0-1 UNRESOLVED（有意延后）**：policy:348-368 仍只校三串，无停服/备份/产物校验；release.yml:516 迁移前无停服、wrapper 无 pg_dump。延后本身可接受（WIP 分支＋已记「上产前必做」），但**登记位置不合格**：只在任务文件 §13，`flow/待办.md` 无对应行 ⇒ 批次归档即蒸发。须进待办索引。
- **P0-2 RESOLVED**：`_classify_managed:493-501` 对 offline-atomic 产「✗ 破坏性 DDL 命中」行 ⇒ rc=1；实测 0192 ⇒ rc=1；release.yml:486 判据 `rc==1 ∧ allow_contract` ⇒ auto_rollback_disabled=true。rollback 恢复门（rollback-tasks.yml）仍缺＝与 P0-1 同批延后。
- **P1-1 RESOLVED**：OFFLINE_ATOMIC_FROZEN:177-180；名单外实测拒（0200_nm）。dummy 值仍能过，但仅限登记名 ⇒ 门槛等同 LEGACY_FROZEN，可接受。
- **P1-2 RESOLVED**：互斥上移至 :332-333（not-hits 早返之前），mix 用例实证。
- **P1-3 PARTIAL**：模块头 :2-8 与 :370 文案已改；`parse_declaration` docstring 仍「4 键」(:285)、`flow/规范/破坏性迁移-两步走.md:48-56` 与 A06:628 仍三通道。
- **P1-4 PARTIAL**：restore=planned / 空 reason / pair / prev-已存在 已钉；**空 backup 未钉**（提交说明称含，实测无 `backup=""` 用例）。

## 新发现（无新 P0）
1. **P1 零命中的 offline-atomic 被误报为「破坏性 DDL 命中」**：实测名单内 + `op.create_table` 的文件 ⇒ rc=1 且打印「offline-atomic 破坏性迁移(不可自动回滚)」，违反模块头「rc=1 语义唯一＝有命中且拒」。修：`_classify_managed:493` 加 `and hits`（或先 `scan_unsafe_lines` 判空）。
2. **P1 P0-2 的修复无自动化钉**：无 check_release 级用例断言「名单内 offline 迁移 ⇒ rc=1」，日后重构可静默退回 rc=0。修：补 1 例（tmp 目录 + 真实 0192 拷贝）。
3. **P1 0192 attestation 是假证据**：`backup="release.yml backup_db ~998MB…"`，但 release.yml 全篇无 backup（grep 空），真备份是 Celery `tasks.py:2506` 定时任务、与发布不绑定。修：改真实指针（路径/任务名/留痕位置）。
4. **P1 部署路径零覆盖**：沙箱三份 fixture 均无 OFFLINE_ATOMIC（0 处）、无 offline 场景 ⇒ 新通道只在 pytest 里证明过。

结论：**NEEDS-REWORK**——P0 已闭、无新 P0；N1/N2/N3 各一行级修＋把两项延后项登记进 `flow/待办.md` 即可再判 PASS。

---

## 终审（2026-11-20，基线 72fd76a）

- **NEW-P1① 零 op 误报**：RESOLVED —— :493 加 `and scan_unsafe_lines(text)`；实测 create_table 文件 rc=0（新用例双钉）。
- **NEW-P1② rc=1 无钉**：RESOLVED —— `test_check_release_offline_atomic_rc` 同时钉 rc==1（破坏）/rc==0（零 op）。
- **NEW-P1③ 0192 假指针**：RESOLVED —— backup 改为「备份任务产出…」，不再指向 release.yml 不存在的步骤（仍属散文，门不可验，接受）。
- **P1-4 空 backup**：RESOLVED（0207_e 用例）。
- **P1-3 docstring**：RESOLVED（:282 六键）；**规范 §3 / A06 UNRESOLVED 但已进 `flow/待办.md` §五 行4** ⇒ 同批延后，可接受。
- **P0-1 / rollback 恢复门**：UNRESOLVED（部署链未动），已登记 `flow/待办.md` §五 行1/2 ⇒ 延后可接受（明示上产前 blocker）。
- **新发现 P1**：ruff 红 —— E702×3（`tests/test_migration_policy.py:721,733,734` 分号并用），仓库收工四件套必绿项；修：半行拆两条语句。

**结论：NEEDS-REWORK（仅此一条机械项，修完即 PASS）**。治理面（名单/仅新文件/互斥/rc=1/attestation/文档/待办）全部闭合。

