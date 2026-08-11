#!/bin/bash
# 量化平台 PG 定时备份 + 保留策略（A5 #26）
# 用法：crontab -e -> 0 2 * * * /home/quant/scripts/backup-db.sh
# 可配环境变量：BACKUP_DIR / DB_NAME / DB_USER / KEEP_DAYS
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/home/quant/backups}"
DB_NAME="${DB_NAME:-quant}"
DB_USER="${DB_USER:-quant}"
KEEP_DAYS="${KEEP_DAYS:-7}"

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

# 删除超过 KEEP_DAYS 的旧备份
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +"$KEEP_DAYS" -delete
echo "[$(date '+%F %T')] 清理 $KEEP_DAYS 天前旧备份完成"
