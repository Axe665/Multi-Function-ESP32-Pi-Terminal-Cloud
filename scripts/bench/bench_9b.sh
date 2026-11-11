#!/usr/bin/env bash
# 9B 报告延迟基准（文档 §11.3）。在云主机上跑，不在 CI 里跑。
# 用法：./scripts/bench/bench_9b.sh [scope] [次数]
#   scope: fleet | edge | device（默认 fleet）
# 依赖：curl、python3；生产环境先 export CLOUD_COOKIE 或 CLOUD_ADMIN_TOKEN。
set -euo pipefail

BASE="${CLOUD_BASE:-http://127.0.0.1:8088}"
SCOPE="${1:-fleet}"
N="${2:-5}"
OUT="${OUT:-$(dirname "$0")/../../docs/bench/9b-$(date +%Y%m%d-%H%M%S).json}"
mkdir -p "$(dirname "$OUT")"

auth=()
[ -n "${CLOUD_ADMIN_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $CLOUD_ADMIN_TOKEN")
[ -n "${CLOUD_COOKIE:-}" ] && auth=(-H "Cookie: $CLOUD_COOKIE")

health="$(curl -s "${auth[@]}" "$BASE/api/v1/health")"
python3 - "$health" <<'PY'
import json, sys
h = json.loads(sys.argv[1])
print(f"gpu={h.get('gpu',{}).get('vendor')} model={h.get('model',{}).get('name')} "
      f"digest={h.get('model',{}).get('digest')} reachable={h.get('model',{}).get('reachable')}")
PY

tmp="$(mktemp)"
for i in $(seq 1 "$N"); do
  start="$(date +%s%N)"
  body="$(curl -s "${auth[@]}" -X POST -H 'content-type: application/json' \
    -d "{\"scope\":\"$SCOPE\",\"mode\":\"local\"}" "$BASE/api/v1/ai/reports")"
  end="$(date +%s%N)"
  python3 - "$tmp" "$start" "$end" "$body" <<'PY'
import json, sys
path, start, end, body = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
try:
    data = json.loads(body)
except ValueError:
    data = {"error": body[:200]}
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps({"ms": round((end - start) / 1e6, 1),
                        "model": data.get("model"), "id": data.get("id"),
                        "error": data.get("error")}, ensure_ascii=False) + "\n")
PY
done

python3 - "$tmp" "$OUT" "$SCOPE" <<'PY'
import json, statistics, sys
rows = [json.loads(x) for x in open(sys.argv[1], encoding="utf-8")]
ms = sorted(r["ms"] for r in rows if not r.get("error"))
def pct(p):
    return round(ms[min(len(ms) - 1, int(len(ms) * p))], 1) if ms else None
out = {"scope": sys.argv[3], "n": len(rows), "ok": len(ms),
       "p50_ms": round(statistics.median(ms), 1) if ms else None,
       "p95_ms": pct(0.95), "rows": rows}
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps({k: out[k] for k in ("n", "ok", "p50_ms", "p95_ms")}, ensure_ascii=False))
print(f"结果：{sys.argv[2]}")
PY
rm -f "$tmp"
echo "判定：p95 ≤ 30000 ms 才算达到 §11.3；模型是 rules-v1 说明 Ollama 没起来，这次不算。"
