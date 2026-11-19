# 批189 M1 实测 runbook（生产体量，prod 内 scratch 库）

> 目的：用**生产数据量**校准迁移预算（0192 红线 40 min），并实测一次 restore 耗时（回滚可行性）。
> 原则：**不把生产数据拷到开发机**（staging 是本机 127.0.0.1，无生产数据）；全部在 prod 内完成，用完即删。

## 0. 谁做什么

| 角色 | 动作 |
|---|---|
| michael（prod root/postgres） | §1 查容量 → §2 建 scratch → §3 跑基线脚本 → 回传 §4 三项输出 |
| bernard | 拿到输出后：校准 40 min、更新 M1 预算、必要时改走基线重置 |

## 1. 容量前置（必须 ≥ 2× 库大小，否则中止）

```
sudo -u postgres psql -d postgres -tAc "select pg_size_pretty(pg_database_size('quant'))"
df -h /var/lib/pgsql /data 2>/dev/null | tail -3
```

## 2. 建 scratch 库并导入（**不动 quant 库**）

```
# dump（一致性快照，不锁业务表；约 930MB 量级）
sudo -u postgres pg_dump -Fc -d quant -f /var/lib/pgsql/quant_189_base.dump

# 建空库 + restore（**不加 -C**：防止误写回 quant 本体），并计时
sudo -u postgres dropdb --if-exists quant_189_scratch
sudo -u postgres createdb -O quant quant_189_scratch
time sudo -u postgres pg_restore -d quant_189_scratch -O -j 4 \
     /var/lib/pgsql/quant_189_base.dump
```

> 注：**绝不要用 `-C`**（它会按 dump 内的库名 `quant` 重建/覆盖）；目标库必须显式写 `-d quant_189_scratch`。

## 3. 跑基线脚本（测量加列/回填/索引的真实耗时）

把仓库里 `scripts/b189_m1_dryrun.sql` 传到 prod（用你们惯用通道，或 `ansible` 的 `copy`），然后：

```
sudo -u postgres psql -d quant_189_scratch -f /tmp/b189_m1_dryrun.sql 2>&1 | tee /tmp/b189_dryrun.log
```

脚本自带护栏：**库名不含 scratch 直接拒绝**；只加列/回填/建索引，不改结构语义。

## 4. 回传三项（贴给我即可）

1. `/tmp/b189_dryrun.log`（含每步 `Time:` 与第 5 段核对行）；
2. `df -h /var/lib/pgsql` 跑完后的剩余空间；
3. §2 的 `pg_restore` 耗时（`real` 行）。

## 5. 判读口径（bernard 用）

- 回填 + 索引两段之和 = 0192 的主要耗时；加上建表/约束重建（估算 5 min）≈ 真实迁移时长。
- 若合计 > 40 min：先看 `order_log/trade_log` 的行数与索引段；仍超 → 改走基线重置或改期。
- `pg_restore` 耗时 = 回滚下限（回滚 = 恢复快照 + 旧代码部署），必须显著小于维护窗余量。

## 6. 清理

```
sudo -u postgres dropdb --if-exists quant_189_scratch
sudo -u postgres rm -f /var/lib/pgsql/quant_189_base.dump
```

---
*本 runbook 只做测量，不执行 0192；0192 落地后在同一 scratch 库复跑一次即为最终验收。*
