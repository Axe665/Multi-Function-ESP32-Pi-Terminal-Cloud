#!/usr/bin/env bash
# 72 h 稳定性采样（文档 §11.3）。默认每 60 s 一次，写 CSV。
# 用法：DURATION_H=72 INTERVAL=60 ./scripts/bench/bench_72h.sh
# 采样内容：健康检查耗时/结果、GPU 厂商、模型 digest、关键容器内存。
set -euo pipefail

BASE="${CLOUD_BASE:-http://127.0.0.1:8088}"
DURATION_H="${DURATION_H:-72}"
INTERVAL="${INTERVAL:-60}"
OUT="${OUT:-$(dirname "$0")/../../docs/bench/72h-$(date +%Y%m%d-%H%M%S).csv}"
mkdir -p "$(dirname "$OUT")"

echo "ts,ok,http_ms,gpu,model_digest,containers" > "$OUT"
end=$(( $(date +%s) + DURATION_H * 3600 ))
fail=0
while [ "$(date +%s)" -lt "$end" ]; do
  start="$(date +%s%N)"
  body="$(curl -s -m 10 -w '\n%{http_code}' "$BASE/api/v1/health" || true)"
  end_ns="$(date +%s%N)"
  code="$(printf '%s' "$body" | tail -n1)"
  json="$(printf '%s' "$body" | sed '$d')"
  ms="$(python3 -c "print(round((${end_ns}-${start})/1e6,1))")"
  row="$(python3 - "$json" <<'PY'
import json, sys
try:
    h = json.loads(sys.argv[1])
except ValueError:
    print("-,-"); raise SystemExit
print(f"{h.get('gpu',{}).get('vendor','-')},{h.get('model',{}).get('digest') or '-'}")
PY
)"
  if [ "$code" != "200" ]; then fail=$((fail+1)); fi
  stats="$(docker stats --no-stream --format '{{.Name}}={{.MemUsage}}' 2>/dev/null | tr '\n' ';' || true)"
  echo "$(date -Is),$code,$ms,$row,$stats" >> "$OUT"
  sleep "$INTERVAL"
done
echo "完成：$OUT（非 200 次数 $fail）"
echo "判定：72 h 内非 200=0、容器无重启、内存无持续上涨才算通过。"
