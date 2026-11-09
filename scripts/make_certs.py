#!/usr/bin/env python3
"""生成 cloud-ca、云的 TLS 证书，并给边签发一张客户端证书。

用法：python3 scripts/make_certs.py [边的证书输出目录]
默认把边要用的 cloud-ca.crt / edge.crt / edge.key 写到
../Modular-Terminal-Edge/docker/cloud-certs/。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cloud import ca  # noqa: E402


def main() -> None:
    out = ROOT / "docker" / "certs"
    edge_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT.parent / "Modular-Terminal-Edge" / "docker" / "cloud-certs"
    domain = os.environ.get("CLOUD_DOMAIN", "localhost")
    edge_id = os.environ.get("EDGE_ID", "edge-lab-01")

    bundle = ca.make_ca()
    server = ca.issue_server(bundle["cert_pem"], bundle["key_pem"], domain,
                             extra_sans=["caddy", "mt-cloud-caddy"])
    client = ca.issue_client(bundle["cert_pem"], bundle["key_pem"], edge_id)
    ca.write_bundle(out, bundle, server, client)

    edge_dir.mkdir(parents=True, exist_ok=True)
    (edge_dir / "cloud-ca.crt").write_text(bundle["cert_pem"])
    (edge_dir / "edge.crt").write_text(client["cert_pem"])
    (edge_dir / "edge.key").write_text(client["key_pem"])
    for f in ("edge.key",):
        (edge_dir / f).chmod(0o644)

    print(f"cloud-ca + {domain} 服务器证书 + {edge_id} 客户端证书")
    print(f"  云： {out}")
    print(f"  边： {edge_dir}")


if __name__ == "__main__":
    main()
