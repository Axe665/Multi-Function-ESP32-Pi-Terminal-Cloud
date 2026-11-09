#!/usr/bin/env bash
# 验收脚本：跑在 docker/compose.yml 起来之后。
# 检查七个服务、队列有没有 worker、AI 报告能不能从队列里出来、备份能不能做。
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
ok()  { printf '  [OK]   %s\n' "$1"; }
bad() { printf '  [FAIL] %s\n' "$1"; fail=1; }
BASE="${CLOUD_URL:-https://localhost}"
code() { curl -sk -o /dev/null -w '%{http_code}' -m 10 "$@"; }

echo "== 服务状态 =="
for s in caddy web api worker postgres redis; do
  state="$(docker compose -f docker/compose.yml ps --format '{{.Service}} {{.State}}' | awk -v s="$s" '$1==s{print $2}')"
  [ "$state" = "running" ] && ok "$s running" || bad "$s ${state:-未启动}"
done
# postgres 必须 healthy，否则 api 起来也会报错
pg_health="$(docker compose -f docker/compose.yml ps --format '{{.Service}} {{.Health}}' | awk '$1=="postgres"{print $2}')"
[ "$pg_health" = "healthy" ] && ok "postgres healthy" || bad "postgres ${pg_health:-no health}"

echo "== 接口 =="
[ "$(code $BASE/api/v1/health)" = "200" ] \
  && ok "health" || bad "health 不通"

curl -sk -m 10 "$BASE/api/v1/health" > /tmp/mt-health.json
python3 - <<'PY' && ok "队列有 worker" || bad "队列没有 worker"
import json, sys
h = json.load(open("/tmp/mt-health.json"))
sys.exit(0 if h.get("queue", {}).get("workers", 0) >= 1 else 1)
PY

python3 - <<'PY' && ok "主库是 Postgres 且可用" || bad "主库不可用"
import json, sys
h = json.load(open("/tmp/mt-health.json"))
sys.exit(0 if h.get("db") == "ok" else 1)
PY

[ "$(code $BASE/api/v1/overview)" = "200" ] \
  && ok "overview" || bad "overview 不通"

echo "== AI 报告（走队列） =="
report="$(curl -sk -m 180 -X POST -H 'content-type: application/json' \
  -d '{"scope":"fleet","task":"weekly_narrative","mode":"rules"}' \
  $BASE/api/v1/ai/reports)"
printf '%s' "$report" | grep -q '"headline"' \
  && ok "报告生成成功" || bad "报告生成失败：${report:0:120}"

echo "== 备份 =="
docker compose -f docker/compose.yml exec -T worker python -c \
  'from cloud import jobs; print(jobs.run_backup())' 2>/dev/null | grep -q "'ok': True" \
  && ok "pg_dump 备份成功" || bad "备份失败"

echo
[ "$fail" = "0" ] && echo "VERIFY: 全部通过" || { echo "VERIFY: 有失败项"; exit 1; }
