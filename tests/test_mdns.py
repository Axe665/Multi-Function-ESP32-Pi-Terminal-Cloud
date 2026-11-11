"""mDNS 的 CA 指纹必须按 DER 算，与边仓同一口径。"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CLOUD_DATA_DIR", tempfile.mkdtemp(prefix="cloud-mdns-"))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from cloud import config, mdns  # noqa: E402


class CaFingerprintTest(unittest.TestCase):
    def test_uses_der_digest_not_file_hash(self):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cloud-test-ca")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        pem = cert.public_bytes(serialization.Encoding.PEM)
        path = Path(tempfile.mkdtemp(prefix="cloud-ca-")) / "cloud-ca.crt"
        path.write_bytes(pem)
        old = config.CA_CERT
        config.CA_CERT = path
        try:
            fp = mdns.ca_fingerprint()
        finally:
            config.CA_CERT = old

        der = cert.public_bytes(serialization.Encoding.DER)
        self.assertEqual(fp, hashlib.sha256(der).hexdigest()[:16])
        # 缺 cryptography 时会退化成 PEM 文件哈希，这两个值必须不同
        self.assertNotEqual(fp, hashlib.sha256(pem).hexdigest()[:16])


if __name__ == "__main__":
    unittest.main()
