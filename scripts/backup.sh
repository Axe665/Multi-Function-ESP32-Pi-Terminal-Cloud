#!/usr/bin/env bash
# 每日全量备份：pg_dump 自定义格式，保留 14 天。
# 用法：./scripts/backup.sh [备份目录]
# 文档 §6.7 要求落外接 USB 盘 —— 这里用 CLOUD_BACKUP_DIR 指过去，
# 没挂盘时默认落在仓库的 ./backups。
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-${CLOUD_BACKUP_DIR:-backups}}"
DSN="${CLOUD_PG_DSN:-postgresql://cloud:cloud@127.0.0.1:5432/cloud}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
TARGET="$OUT/cloud-$STAMP.dump"

pg_dump --format=custom --no-owner --file "$TARGET" "$DSN"

# 校验：能被 pg_restore 读出来才算备份成功
pg_restore --list "$TARGET" >/dev/null

# 保留 14 天
find "$OUT" -name 'cloud-*.dump' -type f -mtime +14 -delete

echo "已备份 $(du -h "$TARGET" | cut -f1) 到 $TARGET，当前保留 $(find "$OUT" -name 'cloud-*.dump' | wc -l) 份"
