#!/usr/bin/env bash
# 把 _orpi-cloud._tcp 的广播交给宿主 Avahi。
# 容器没有 host 网络，python-zeroconf 广播的是容器 IP，边发现不到云。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
CLOUD_ID=${CLOUD_ID:-cloud-home-01}
PORT=8443
CA=${CLOUD_CA_CERT:-docker/certs/cloud-ca.crt}
DOMAIN=${CLOUD_DOMAIN:-}

if [ ! -f "$CA" ]; then
  echo "找不到 cloud-ca.crt：$CA" >&2
  exit 1
fi
FP=$(openssl x509 -in "$CA" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f' | cut -c1-16)

if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
  URL="https://$DOMAIN:$PORT"          # 域名形式：SNI 才过得了网关
else
  IP=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p' | head -1)
  URL="https://${IP:-127.0.0.1}:$PORT"
  echo "没设 CLOUD_DOMAIN，TXT 里用 IP 形式（$URL）；部分网关对 IP 做 SNI 会 421" >&2
fi

OUT=/etc/avahi/services/orpi-cloud.service
sed -e "s|@CLOUD_ID@|$CLOUD_ID|" -e "s|@PORT@|$PORT|" \
    -e "s|@CA_FP@|$FP|" -e "s|@URL@|$URL|" \
  "$ROOT/deploy/avahi/orpi-cloud.service.tmpl" | sudo tee "$OUT" >/dev/null
sudo systemctl restart avahi-daemon
echo "已发布 _orpi-cloud._tcp（id=$CLOUD_ID, $URL, ca_fp=$FP）"
echo "边侧可以这样验证：avahi-browse -rt _orpi-cloud._tcp"
