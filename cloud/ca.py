"""cloud-ca：给云自己的 TLS 入口和边缘节点签发证书。

文档 §5.7：边信任 cloud-ca，边证书由 cloud-ca 签发。
私钥只在云侧生成，边缘节点拿到的只有证书与自己的私钥。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_CN = "modular-terminal-cloud-ca"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _pem_key(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def make_ca(days: int = 3650) -> dict[str, Any]:
    key = _key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_CN),
                      x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "mt-cloud")])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(minutes=5))
            .not_valid_after(_now() + dt.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    return {"cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "key_pem": _pem_key(key)}


def _issue(ca_cert: x509.Certificate, ca_key: ec.EllipticCurvePrivateKey,
           common_name: str, sans: list[str], usage: x509.ObjectIdentifier,
           days: int) -> dict[str, str]:
    key = _key()
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,
                           "mt-edge" if usage == ExtendedKeyUsageOID.CLIENT_AUTH else "mt-cloud"),
    ])
    builder = (x509.CertificateBuilder()
               .subject_name(subject).issuer_name(ca_cert.subject)
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(_now() - dt.timedelta(minutes=5))
               .not_valid_after(_now() + dt.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.ExtendedKeyUsage([usage]), critical=False))
    if sans:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
    cert = builder.sign(ca_key, hashes.SHA256())
    return {"cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "key_pem": _pem_key(key)}


def issue_server(ca_cert_pem: str, ca_key_pem: str, name: str,
                 extra_sans: list[str] | None = None, days: int = 825) -> dict[str, str]:
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode())
    ca_key = serialization.load_pem_private_key(ca_key_pem.encode(), password=None)
    return _issue(ca_cert, ca_key, name, [name, "localhost", *(extra_sans or [])],
                  ExtendedKeyUsageOID.SERVER_AUTH, days)


def issue_client(ca_cert_pem: str, ca_key_pem: str, edge_id: str,
                 days: int = 365) -> dict[str, str]:
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode())
    ca_key = serialization.load_pem_private_key(ca_key_pem.encode(), password=None)
    return _issue(ca_cert, ca_key, edge_id, [], ExtendedKeyUsageOID.CLIENT_AUTH, days)


def write_bundle(out: Path, ca: dict[str, str], server: dict[str, str],
                 client: dict[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "cloud-ca.crt").write_text(ca["cert_pem"])
    (out / "cloud-ca.key").write_text(ca["key_pem"])
    (out / "cloud.crt").write_text(server["cert_pem"])
    (out / "cloud.key").write_text(server["key_pem"])
    (out / "edge.crt").write_text(client["cert_pem"])
    (out / "edge.key").write_text(client["key_pem"])
    # 容器里的 Caddy 以非 root 用户跑，读不到 0600 的文件；目录本身也不进仓库
    for f in ("cloud-ca.key", "cloud.key", "edge.key"):
        (out / f).chmod(0o644)
