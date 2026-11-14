"""证书 SAN：内网 IP 必须写成 IPAddress，写成 DNSName 浏览器不认。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography import x509  # noqa: E402

from cloud import ca  # noqa: E402


class SanTest(unittest.TestCase):
    def test_ip_and_dns_sans(self):
        bundle = ca.make_ca()
        server = ca.issue_server(bundle["cert_pem"], bundle["key_pem"], "cloud.lan",
                                 extra_sans=["caddy", "192.168.31.63", "cloud-home-01"])
        cert = x509.load_pem_x509_certificate(server["cert_pem"].encode())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns = san.get_values_for_type(x509.DNSName)
        self.assertIn("cloud.lan", dns)
        self.assertIn("cloud-home-01", dns)
        self.assertEqual([str(ip) for ip in san.get_values_for_type(x509.IPAddress)],
                         ["192.168.31.63"])


if __name__ == "__main__":
    unittest.main()
