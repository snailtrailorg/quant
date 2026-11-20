# 批189 迁移闸门 offline-atomic · 盲审 B（独立，未读 A 报告）
范围 5b2aa06 vs 86a81fb。结论：**NEEDS-REWORK**——该通道把 LEGACY_FROZEN 同型软绕过以更宽形态复刻。

**P0-1 门从「验证」退回「信任」：无文件名白名单** — server/src/data_platform/migration_policy.py:331-350
实测：任一*全新*迁移写 `phase=offline-atomic reason="." backup="/nonexistent/never.dump" restore=rehearsed` ⇒ evaluate=True、check_release rc=0。同文件 :195 LEGACY_FROZEN 判据原文「自声明标记只凭标记放行＝比 allow_contract 更易达的零留痕绕过」正是此形态，而本条连冻结集都没有；:333 注释称「防成 LEGACY_FROZEN 式软绕过」与实现相反。
修：加 `OFFLINE_ATOMIC_FROZEN={"0192_b189_interface_split.py"}`，_classify_managed 按名验证（集外即拒、只减不增）＋双向守门用例。

**P0-2 已部署迁移的就地改写被打通（不可变约定失效）** — migration_policy.py:331 分支不读 prev_dir
实测：prev 已有 0200_x.py，新版 upgrade 改 drop_table + 该头 ⇒ check_release rc=0；改动前同文件须 contract+prev 内 expand（必红）。
修：offline 分支要求 `prev_dir is None or name not in prev`（仅限全新文件）。

**P1-1 attestation 是散文不是指针** — migration_policy.py:338-346
`backup="/nonexistent/never.dump"` 通过；`restore=rehearsed` 为自由 token。0192:1 称 `backup="release.yml backup_db…"`——release.yml 全篇无 backup 阶段（grep 空），真实备份在 server/src/scheduler/tasks.py:2506（/home/quant/backups 保 3 份），与发布无绑定 ⇒ 回滚安全性的替代证据为零。
修：backup 须为存在且 size>0 的绝对路径（门内校验），0192 改指真实产物；restore 同样指向可校验的演练留痕。

**P1-2 回滚侧未机械化，「快照恢复+旧代码」只是 runbook** — deploy/playbooks/rollback-tasks.yml:183-203
翻回旧代码后才 compat 复验且 failed_when:false，不恢复 DB——旧代码已先对新 schema 起进程；门的前向保护在回滚时无等价物。
修：offline-atomic 发布落 var/ 的 restore-required 标记，rollback.yml 见标记即拒 symlink-only 回滚。

**P1-3 声明真源仍写「唯一通道」** — flow/规范/破坏性迁移-两步走.md:48-56；migration_policy.py:349 文案、:265 parse_declaration docstring（仍 4 键）；test_migration_expand_contract.py:4
修：按仓库规矩整篇重写上述节/文案/docstring，纳入 offline-atomic 与 6 键返回。

**P1-4 部署场景沙箱内嵌旧模块** — deploy/tests/sandbox_root/quant/releases/*/src/data_platform/migration_policy.py 无新分支 ⇒ S2/S7 不覆盖新通道（pytest 不在部署路径）
修：刷新夹具并加一条 offline 场景（或断言夹具与真源 sha256 一致）。

对问题(1)：offline-atomic 仅在「全停 / 备份可验 / 回滚真做恢复」三条同时成立时等价；后两条当前无机械证据 ⇒ 是「声明替代保护」，非等价保护。
**NEEDS-REWORK**
