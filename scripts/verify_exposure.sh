#!/usr/bin/env bash
# 文档 §6.5 / §11.1：公网入口只放行 /api/v1/ingest/edge-report 与 /api/v1/health。
# 用 cloud-ca 签的边客户端证书连 8443（边→云 mTLS 口）逐条验证：
# 两条路由通，其余一律 403；不带客户端证书直接连不上。
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BASE=${PUBLIC_BASE:-https://localhost:8443}
INNER=${INNER_BASE:-https://localhost}
CA=$ROOT/docker/certs/cloud-ca.crt
CERT=$ROOT/docker/certs/edge.crt
KEY=$ROOT/docker/certs/edge.key
BODY=$(mktemp)
trap 'rm -f "$BODY"' EXIT

fail=0
ok() { echo "  [PASS] $1"; }
bad() { echo "  [FAIL] $1"; fail=1; }
code() { curl -sk -o "$BODY" -w '%{http_code}' -m 10 "$@"; }
mtls=(--cacert "$CA" --cert "$CERT" --key "$KEY")

for f in "$CA" "$CERT" "$KEY"; do
  [ -f "$f" ] || { echo "[FAIL] 缺少 $f，先跑 scripts/make_certs.py"; exit 1; }
done

c=$(code "${mtls[@]}" "$BASE/api/v1/health")
[ "$c" = "200" ] && ok "健康检查可达（200）" || bad "健康检查返回 $c"

# 白名单里的上报路由：令牌/载荷由 api 判，Caddy 不该自己回 403
c=$(code "${mtls[@]}" -X POST -H 'content-type: application/json' -d '{}' \
    "$BASE/api/v1/ingest/edge-report")
if [ "$c" = "403" ] || grep -q "not exposed" "$BODY"; then
  bad "上报路由被挡在 Caddy（$c）"
else
  ok "上报路由可达，交给 api 判令牌与载荷（$c）"
fi

for p in /api/v1/overview /api/v1/edges /api/v1/devices /api/v1/series \
         /api/v1/privacy/audit /api/v1/ai/reports /api/v1/auth/state; do
  c=$(code "${mtls[@]}" "$BASE$p")
  [ "$c" = "403" ] && ok "$p 被挡（403）" || bad "$p 返回 $c，公网不该放行"
done

c=$(code --cacert "$CA" "$BASE/api/v1/health")
[ "$c" = "000" ] && ok "不带客户端证书连不上（要求 mTLS）" || bad "不带证书也连得上：$c"

# 反证：被挡的是 8443，不是整个 api；管理台在内网入口照旧
c=$(code --cacert "$CA" "$INNER/api/v1/overview")
case "$c" in
  200|401) ok "内网入口的管理 API 照旧（$c）" ;;
  *) bad "内网入口也访问不了管理 API：$c" ;;
esac

echo
[ "$fail" = "0" ] && echo "EXPOSURE: 全部通过" || { echo "EXPOSURE: 有失败项"; exit 1; }
