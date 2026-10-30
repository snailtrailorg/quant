#!/bin/bash
# 量化平台 PG 定时备份 + 保留策略（A5 #26）
# 用法：crontab -e -> 0 2 * * * /home/quant/scripts/backup-db.sh
# 可配环境变量：BACKUP_DIR / DB_NAME / DB_USER / KEEP_COUNT（保留份数，默认 3）
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/home/quant/backups}"
DB_NAME="${DB_NAME:-quant}"
DB_USER="${DB_USER:-quant}"
KEEP_COUNT="${KEEP_COUNT:-3}"   # 2026-10-31 用户裁定：按份数保留 3（原按天 5——天数法在停机或手动补跑时会积压或误清）

mkdir -p "$BACKUP_DIR"

TS=$(date +%Y%m%d_%H%M%S)
FILE="$BACKUP_DIR/${DB_NAME}_${TS}.sql.gz"

# pg_dump + gzip（失败则退出，set -e）
if pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$FILE"; then
  SIZE=$(du -h "$FILE" | cut -f1)
  echo "[$(date '+%F %T')] 备份完成: $FILE ($SIZE)"
else
  echo "[$(date '+%F %T')] 备份失败: $DB_NAME" >&2
  rm -f "$FILE"
  exit 1
fi

# 保留最新 KEEP_COUNT 份，删除其余（文件名含时间戳天然有序，降序即新到旧）
ls -1 "$BACKUP_DIR/${DB_NAME}_"*.sql.gz 2>/dev/null | sort -r | tail -n +"$((KEEP_COUNT + 1))" | while read -r old; do rm -f "$old"; done
echo "[$(date +%F_%T)] 保留策略: 最新 $KEEP_COUNT 份"
