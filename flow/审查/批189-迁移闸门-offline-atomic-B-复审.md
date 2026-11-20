# 批189 迁移闸门 · 复审 B（37bd02d）
结论：**PASS（带 2 条上产硬条件）**；前轮 2×P0 均实证闭合，残余为 P1 与部署链欠账。

- **P0-1 RESOLVED**：OFFLINE_ATOMIC_FROZEN 名单（migration_policy.py:177-181）+ 集外拒（:349-352）；实测非名单文件（伪造 attestation）现拒，名单内旧行为保留。
- **P0-2 RESOLVED**：prev 同名即拒「仅限全新迁移」（:353-355），单位用例已钉（tests/test_migration_policy.py:712-715）；实测 prev 含同名文件 → 拒。
- **P1-1 UNRESOLVED（降级为发布前置）**：backup 仍为任意散文（"b" 通过）、0192:1 仍指 release.yml 中不存在的 backup_db 阶段；机器证据仍为零。归入部署链条件 (a)。
- **P1-2 UNRESOLVED（降级为发布前置）**：rollback-tasks.yml:183-203 未改，仍先翻旧代码后检撕裂、不恢复 DB。归入条件 (b)。
- **P1-3 PARTIAL**：模块头/phase=None 文案/test docstring 已改；但 :282 parse_declaration docstring 仍 4 键、职责 1-5 未提第二通道、:26「0=全部合规」、flow/规范 §3 与 A06 未改——仍两处真源，须随批重写。
- **P1-4 UNRESOLVED**：deploy/tests/sandbox_root 内嵌旧模块，S2/S7 不覆盖新通道（未登记为阻塞项，建议随条件 (a) 刷夹具）。

**NEW P1（必须随条件 (a) 闭合）**：rc=1 使 allow_contract 成为 offline-atomic 的**必经**旗标（release.yml:451/478-486），而该旗标对整版生效 ⇒ 同发布里另一条未登记的破坏性迁移被一并豁免、且与「合规 offline」不可区分。修：豁免改按键生效（门输出区分 offline/普通命中，release.yml 仅当全部命中皆 offline 时放行），或独立 `offline_window` 旗标；否则 0192 发布即把逃生门常态化。

**新增部署链条件（非本模块可解，判定为可接受的上产前阻塞项）**：
(a) release.yml 阶段 4 强制离线前提 + 备份产物存在校验（否则「离线」只是文件名自述，P1-1 永远自由文本）；
(b) rollback 翻旧代码前须验/恢复快照并落 restore-required 标记（否则 B P1-2 的门在回滚侧无等价物）。
两项均须 A06 评审后改管道，先于 0192 首次 release 落地；若批次要声明「治理通道可用」而不待 (a)(b)，则改判 NEEDS-REWORK。

**PASS**（条件：P1-3 文档随批重写 + NEW P1 随 (a) 修；0192 release 前 (a)(b) 必须已上）。
