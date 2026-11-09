#!/usr/bin/env bash
# 本地跑起来（单进程，不用 docker）：
#   1. Postgres 与 Redis 已在跑（可以只起这两个容器：docker compose up -d postgres redis）
#   2. ./run.sh        库是空的时候自动写 14 天演示数据
# 生产部署见 docker/compose.yml（caddy + web + api + worker + postgres + redis）。
set -euo pipefail
cd "$(dirname "$0")"

export CLOUD_PG_DSN="${CLOUD_PG_DSN:-postgresql://cloud:cloud@127.0.0.1:5432/cloud}"
export CLOUD_REDIS_URL="${CLOUD_REDIS_URL:-redis://127.0.0.1:6379/0}"

python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from cloud import store
store.init_db()
print("db ok, edges =", len(store.edges()))
PY

if [ "${CLOUD_AUTOSEED:-1}" = "1" ]; then
  python3 -m cloud.seed
fi

# worker 负责 AI 报告与定时任务（文档 §6.1）
if [ "${CLOUD_WORKER:-1}" = "1" ]; then
  python3 -m cloud.worker &
  trap 'kill %1 2>/dev/null || true' EXIT
fi

exec python3 -m uvicorn cloud.main:app --host 0.0.0.0 --port "${PORT:-8002}"
