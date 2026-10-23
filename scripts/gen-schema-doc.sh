#!/usr/bin/env bash
# 从备份 dump 生成表结构参考（批 143 A 段顺产，无需起库）
# 用法: bash scripts/gen-schema-doc.sh <dump.gz> [输出=docs/design/D18-表结构参考-生成.md]
# 原理: zcat 抽取 CREATE TABLE 块与 COMMENT ON 行，按表聚合。结构说明不脱敏；无数据行。
set -euo pipefail
DUMP="${1:?用法: gen-schema-doc.sh <dump.gz> [输出]}"
OUT="${2:-docs/design/D18-表结构参考-生成.md}"
TMP=$(mktemp -d)
zcat "$DUMP" | awk -v tmp="$TMP" '
  /^CREATE TABLE /{inblk=1; n++; f=sprintf("%s/tbl_%04d.sql", tmp, n)}
  inblk{print > f; if (/;[[:space:]]*$/) inblk=0}
  /^COMMENT ON TABLE /{print > (tmp "/tblcomments.txt")}
  /^COMMENT ON COLUMN /{print > (tmp "/colcomments.txt")}
'
BT=$(printf '\140')
NOW=$(date '+%F %H:%M')
NTBL=$(ls "$TMP"/tbl_*.sql | wc -l)
{
  echo "# D18 附录 · 数据库表结构参考（生成式）"
  echo
  echo "> 生成于 $NOW，来源=最新备份 dump 的 DDL（zcat 抽取 CREATE TABLE/COMMENT ON）。"
  echo "> 生成命令：${BT}bash scripts/gen-schema-doc.sh <dump.gz>${BT}——**随每次演练/每批迁移重跑**，勿手改。"
  echo "> 结构说明不脱敏（无密钥值、无数据行）；行数估计见 ${BT}flow/稳定性检查/批143-磁盘取证.md${BT}。"
  echo
  echo "表总数：$NTBL"
  for f in $(ls "$TMP"/tbl_*.sql | sort); do
    TBL=$(head -1 "$f" | sed -E 's/^CREATE TABLE (public\.)?([^ (]+).*/\2/')
    echo
    echo "## $TBL"
    echo
    echo "${BT}${BT}${BT}sql"
    cat "$f"
    echo "${BT}${BT}${BT}"
    grep -hE "COMMENT ON (TABLE|COLUMN) [^ ]*[.]$TBL[ (,]" "$TMP"/tblcomments.txt "$TMP"/colcomments.txt 2>/dev/null | sed 's/^/- /' || true
  done
} > "$OUT"
rm -rf "$TMP"
echo "已生成: $OUT（$(wc -l < "$OUT") 行）"
