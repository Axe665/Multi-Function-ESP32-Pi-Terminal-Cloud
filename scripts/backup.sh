#!/usr/bin/env bash
# SQLite 在线备份：不锁库，保留 14 天。用法：./scripts/backup.sh [备份目录]
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${CLOUD_DB:-data/cloud.db}"
OUT="${1:-data/backups}"
[ -f "$DB" ] || { echo "找不到 $DB"; exit 1; }
mkdir -p "$OUT"
STAMP="$(date +%Y-%m-%d)"
sqlite3 "$DB" ".backup '$OUT/cloud-$STAMP.db'"
ls -1t "$OUT"/cloud-*.db | tail -n +15 | xargs -r rm -f
echo "已备份到 $OUT/cloud-$STAMP.db，当前保留 $(ls -1 "$OUT" | wc -l) 份"
