"""Caddyfile 的信任边界：CN 头只能在做过 client_auth 的监听上被采信。

应用层的 edge_guard 直接读 X-Client-CN，这是设计（中转方负责验签）。所以
「哪个监听会透传客户端自带的头」必须由测试钉住——443 一旦又变成万能入口，
权限模型就整体失效，而单元测试看不到这一层。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

CADDY = Path(__file__).resolve().parent.parent / "docker/Caddyfile"


def _block(text: str, header: str) -> str:
    """取出以 header 开头、到行首 `}` 为止的整段。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == header.strip():
            depth, out = 0, []
            for line2 in lines[i:]:
                out.append(line2)
                depth += line2.count("{") - line2.count("}")
                if depth == 0 and len(out) > 1:
                    return "\n".join(out)
    raise AssertionError(f"Caddyfile 里找不到这个块：{header}")


class CaddyTrustBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CADDY.read_text(encoding="utf-8")

    def test_ingest_is_refused_on_the_plain_tls_listener(self):
        """443 没有 client_auth，必须显式拒绝摄取，不能落进 /api/* 的兜底。"""
        block = _block(self.text, "{$CLOUD_DOMAIN:localhost} {")
        # 看指令本身，不看注释（注释里也提到这个词）
        self.assertIsNone(re.search(r"^\s*client_auth\s*\{", block, re.M),
                          "443 监听不该要求客户端证书，所以更不能放摄取进来")
        self.assertIn("/api/v1/ingest/*", block)
        ingest_at = block.index("/api/v1/ingest/*")
        api_at = block.index("handle /api/*")
        self.assertLess(ingest_at, api_at, "拒绝摄取的路由必须排在 /api/* 前面")
        refuse = block[ingest_at:api_at]
        self.assertIn("403", refuse)

    def test_plain_listener_clears_the_cn_header(self):
        """兜底：即使以后有人加了信 CN 的接口，443 也不许把这个头传上去。"""
        block = _block(self.text, "{$CLOUD_DOMAIN:localhost} {")
        self.assertRegex(block, r'header_up\s+X-Client-CN\s+(""|\'\')')

    def test_mtls_listener_sets_the_cn_header(self):
        """8443 必须自己写这个头，且信任来自已验过的证书链。"""
        block = _block(self.text, ":8443 {")
        self.assertIn("require_and_verify", block)
        self.assertRegex(block, r"header_up\s+X-Client-CN\s+\{http\.request\.tls\.client\.subject\}")

    def test_ingest_route_only_exists_on_the_mtls_listener(self):
        """摄取路径在整个文件里只应出现在 8443 那个块里（加上 443 的拒绝规则）。"""
        hits = [m.start() for m in re.finditer(r"handle /api/v1/ingest/edge-report", self.text)]
        self.assertEqual(len(hits), 1, "edge-report 只该有一个反代入口")
        self.assertIn(":8443 {", self.text[: hits[0]])


if __name__ == "__main__":
    unittest.main()
