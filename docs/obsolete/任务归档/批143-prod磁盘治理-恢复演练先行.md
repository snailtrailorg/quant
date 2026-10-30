# 批 143 · prod 磁盘治理：恢复演练先行（A 取证）→ 证据驱动清理（B）

> 立项 2026-10-23（用户裁定）：prod 磁盘紧张，PG 目录 12G vs 单份备份 <1G（~736M）差一个量级——疑有垃圾但**先不定性**；同日用户观察 **quant 部署根目录 4.2G**（依赖轮子切片还是垃圾？）一并纳入。
> **次序立法（用户风险顾虑＝排序依据）**：不是「演练顺便清理」，而是「演练给清理发通行证」——备份完整性未证实前不动任何删除。
> 吸收既有挂账：10-20 演练未竟的「独立库 restore 全流程」（原待办行已并入本批，踩坑记录 2026-10-20 条为过程判据真源）。

## 现状（已实证部分）
- 备份链：`backup-db.sh` 稳定副本 `/home/quant/scripts/`（不进 releases）→ `/home/quant/backups/`，crontab 02:00，保留 7 天，dump ~736M（2026-09-08 装位，实测记录见 server-info 记忆）
- 10-20 dev 演练：dump 可用性三级验证**已过**（gzip -t / 2446 CREATE+108 COPY 段 / sync_config 游标实抽）；**独立库 restore 未走通**（本机 CREATEDB 权限卡死）——prod 侧 michael/root 通道本就持有
- prod：PG18，PG 目录 12G（用户 2026-10-23 观察）；12x 压缩比高于时序数据常态（4-8x）⇒ 疑膨胀叠加

## 疑因三解释（A 段取证定谳，不预先站队）
1. **表膨胀**（最可能）：system_metric/sync_log/health_event/audit_log/risk_log 高频写表，prune 删行后普通 VACUUM 只复用页内、**高水位永不还 OS**
2. **WAL 累积**：死 replication slot / archive 失败可无限涨（一条 SQL 可判）
3. **压缩比正常**：时序数值列 gzip 单独解释不了 12x，最多与 1 叠加

## A 段：恢复演练 + 取证（**零删除**）
1. **restore 正身**：取 prod 最新 dump → 同机临时库 restore（michael/root 通道，CREATEDB 有）——10-20 挂账收口
2. **三级验证升级版**：①gzip -t ②DDL+COPY 段计数 ③关键表**行数与游标实抽、与库内实况对数**（sync_config 游标值、security_master 标的数、bar_1d 行数——以 dump 时点为锚）——绿＝备份完整性证实
3. **取证清单**（全部只读）：du 分解（PGDATA/base 每库、pg_wal、log/temp）｜pg_stat_user_tables top20（n_dead_tup/n_live_tup/last_autovacuum）｜pg_replication_slots 活性与 lag｜pg_stat_database.temp_files/temp_bytes｜pg_total_relation_size top20｜**quant 部署根 du -d2 分解**（releases×N 各自构成——deps/vendor 轮子切片是嫌疑大头；shared/venv、shared/runtime、var、backups 磁盘占用）
4. **表结构完整本地副本（顺产）**：restore 临时库上经 information_schema 导出全量表结构（表/列/类型/注释/行数估计）→ `docs/design/D18-表结构参考-生成.md`（生成式，文件头写生成命令；init-schema.sql 只覆盖核心 19 节、schema_expectations.txt 只有列名无类型——此件补全，随演练重跑维护）

## B 段：证据驱动清理（**A 绿后**，逐项按证据裁定）
1. 无损先行：WAL 归因（死 slot→drop、archive 断点→修）→ CHECKPOINT；temp/旧日志清理
2. 膨胀表：按死元组比例排序，低峰窗逐表回收（VACUUM FULL 或换表法；pg_repack 需先查 PG18 扩展支持）——清单=A 段产出
3. **quant 根目录**：releases 内 deps/vendor 轮子切片跨版本重复（GC 保留 N=5 ⇒ 最多 5 份）——候选=旧 release 只留代码不留轮子/降 GC 保留数/轮子上移 shared 只读复用；**改 GC/切片前必读 A06 §9 炸半径**（wrapper/白名单联动）——按取证数字裁定
4. 验收：PG 目录+quant 根回落数字＋**备份体积应明显变小**（活行口径，这本身是清理效果验收）＋healthz/hub 心跳全绿
5. 风险闸：VACUUM FULL 锁表 ⇒ 低峰窗逐表；每表回收前后行数对账；releases 清理走既有 GC 通道不自创删除；任一步异常即停（备份已证实=可兜底）

## 限定范围
只动 prod 磁盘治理面；不碰 schema、不碰代码、不碰备份保留策略（7 天×736M≈5G 的策略问题另议）。B 段逐表裁定、清单外不动。

## 引用
- flow/踩坑记录.md 2026-10-20 条 —— 三级验证法（固定动作）+ 权限坑（本机 md5/trust 差异）
- docs/design/D18-数据库操作规范.md —— 锁链事件根治/长事务告警（VACUUM FULL 前必读）
- 记忆 server-info —— 备份链落点（目录/crontab/脚本三查口径）

## 接口契约
无新增代码接口。B 段若用换表法：`CREATE TABLE <t>_new AS SELECT ... → rename swap → drop old`（同一事务，读写表=涉事表自身）。

## 回滚语义
A 段零删除（临时库用后即删）。B 段：VACUUM FULL/换表法本质不可逆但以「A 段证实的新鲜备份」为兜底；执行窗内 fail 即停。

## 验收标准
1. A：临时库 restore 成功＋三级对数全过（备份完整性结论=可/不可兜底，二值）
2. A：取证报告落档（12G 的构成给出数字分解：活数据/WAL/temp/膨胀各多少；4.2G 的构成：releases/shared/var 各多少、轮子切片占几成）
3. B：清单逐项执行前后对账；PG 目录回落至「活数据+合理水位」
4. B：清理后 backup-db.sh 手跑一轮，dump 体积对照
5. 全程 healthz/quant-hbcheck 绿；无长事务告警（D18 R7）

## mock 方式
无——全部真库操作；取证 SQL 即验证载体。
