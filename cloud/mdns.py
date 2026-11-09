"""云广播 _orpi-cloud._tcp（文档 §5.9 / §6.1），边据此发现云。

TXT 只放发现所需的 ID / 版本 / 端口 / CA 指纹；没有组播时静默退化，
静态地址（EDGE_CLOUD_URL 或 cloud.json）继续兜底。
"""
from __future__ import annotations

import hashlib
import socket
from typing import Any

try:
    from zeroconf import ServiceInfo, Zeroconf
except ImportError:  # pragma: no cover
    Zeroconf = None  # type: ignore[assignment]

from . import config

CLOUD_TYPE = "_orpi-cloud._tcp.local."


def ca_fingerprint() -> str:
    """取 CA 证书 DER 的 SHA256 前 16 hex，与边的 ca_fp 同一口径。"""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_pem_x509_certificate(config.CA_CERT.read_bytes())
        der = cert.public_bytes(serialization.Encoding.DER)
        return hashlib.sha256(der).hexdigest()[:16]
    except Exception:  # noqa: BLE001 —— 只用于发现，读不到就不写指纹
        try:
            return hashlib.sha256(config.CA_CERT.read_bytes()).hexdigest()[:16]
        except OSError:
            return ""


def cloud_txt() -> dict[bytes, bytes]:
    return {
        b"v": b"1",
        b"id": config.CLOUD_ID.encode(),
        b"port": str(config.MDNS_PORT).encode(),
        b"ca_fp": ca_fingerprint().encode(),
    }


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Mdns:
    def __init__(self) -> None:
        self.zc: Any = None
        self.info: Any = None
        self.error: str | None = None

    def start(self) -> bool:
        if Zeroconf is None or not config.MDNS_ENABLED:
            self.error = "zeroconf 未安装或已关闭"
            return False
        try:
            self.zc = Zeroconf()
            self.info = ServiceInfo(
                CLOUD_TYPE,
                f"{config.CLOUD_ID}.{CLOUD_TYPE}",
                addresses=[socket.inet_aton(_lan_ip())],
                port=config.MDNS_PORT,
                properties=cloud_txt(),
                server=f"{config.CLOUD_ID}.local.",
            )
            self.zc.register_service(self.info)
            self.error = None
            return True
        except Exception as e:  # noqa: BLE001 —— 组播被禁用不能拖垮服务
            self.error = str(e)[:200]
            return False

    def stop(self) -> None:
        if self.zc:
            if self.info:
                self.zc.unregister_service(self.info)
            self.zc.close()
        self.zc = None
        self.info = None

    def status(self) -> dict[str, Any]:
        return {"on": self.zc is not None, "error": self.error,
                "id": config.CLOUD_ID, "port": config.MDNS_PORT,
                "ca_fp": ca_fingerprint()}


mdns = Mdns()
