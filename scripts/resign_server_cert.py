#!/usr/bin/env python3
"""只重签服务器证书，CA 保持不变。

`make_certs.py` 会新生成一颗 CA，跑一次就等于换了信任根：边的
`EDGE_CLOUD_CA` 指纹、mDNS 的 `ca_fp`、8443 的 trust_pool 全都对不上。
换网段、加内网 IP 或内部 DNS 时用这个脚本，别用 make_certs。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cloud import ca  # noqa: E402


def main() -> int:
    out = ROOT / "docker" / "certs"
    ca_crt, ca_key = out / "cloud-ca.crt", out / "cloud-ca.key"
    if not ca_crt.exists() or not ca_key.exists():
        print(f"找不到 {ca_crt} 或 {ca_key}；首次部署请先跑 make_certs.py", file=sys.stderr)
        return 1

    domain = os.environ.get("CLOUD_DOMAIN", "localhost")
    sans = ["caddy", "mt-cloud-caddy"]
    for key in ("CLOUD_ID", "CLOUD_LAN_IP", "CLOUD_DNS"):
        value = os.environ.get(key, "").strip()
        if value and value not in sans:
            sans.append(value)

    server = ca.issue_server(ca_crt.read_text(encoding="utf-8"),
                             ca_key.read_text(encoding="utf-8"), domain, extra_sans=sans)
    (out / "cloud.crt").write_text(server["cert_pem"])
    (out / "cloud.key").write_text(server["key_pem"])
    # Caddy 以非 root 跑，站点私钥必须可读
    (out / "cloud.key").chmod(0o644)

    from cryptography import x509

    cert = x509.load_pem_x509_certificate((out / "cloud.crt").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    print(f"已重签 {domain}（CA 未变）：")
    print("  SAN:", ", ".join(str(v) for v in san))
    print("重载：docker compose -f docker/compose.yml up -d --force-recreate caddy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
