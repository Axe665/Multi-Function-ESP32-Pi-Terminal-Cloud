"""管理员登录：TOTP 向量、会话签名、接口门禁。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="cloud-auth-")
os.environ.setdefault("CLOUD_DATA_DIR", _TMP)
os.environ.setdefault("CLOUD_AI_MODE", "rules")

from fastapi.testclient import TestClient  # noqa: E402

from tests.support import ensure_pg  # noqa: E402

ensure_pg()

from cloud import auth, config, store  # noqa: E402
from cloud.main import app  # noqa: E402

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 的测试密钥


class TotpTest(unittest.TestCase):
    def test_rfc_vector(self):
        # RFC 6238 附录 B：T=59，SHA1，8 位 → 94287082
        self.assertEqual(auth.totp(SECRET, at=59, digits=8), "94287082")
        self.assertEqual(auth.totp(SECRET, at=59), "287082")

    def test_window_tolerance(self):
        code = auth.totp(SECRET, at=1000)
        self.assertTrue(auth.verify_totp(SECRET, code, at=1000))
        self.assertTrue(auth.verify_totp(SECRET, code, at=1000 + 30))   # 前后各一窗口
        self.assertFalse(auth.verify_totp(SECRET, code, at=1000 + 120))  # 超出窗口
        self.assertFalse(auth.verify_totp(SECRET, "000000", at=1000))


class SessionTest(unittest.TestCase):
    def test_sign_and_verify(self):
        token = auth.issue_session()
        self.assertTrue(auth.check_session(token))
        self.assertFalse(auth.check_session(token[:-1] + ("0" if token[-1] != "0" else "1")))
        self.assertFalse(auth.check_session(None))
        self.assertFalse(auth.check_session("garbage"))

    def test_expired_session_rejected(self):
        import time
        self.assertFalse(auth.check_session(auth.issue_session(int(time.time()) - 999999)))


class GuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()
        cls.client = TestClient(app)

    def setUp(self):
        self.client.cookies.clear()   # 用例之间不共享会话
        config.ADMIN_PASSWORD = "correct horse"
        config.ADMIN_TOTP = SECRET
        config.ADMIN_TOKEN = ""
        # 每个用例都从「没登录过」开始：TOTP 时间步是一次性的
        store.set_counter("admin_totp_step", 0)

    def tearDown(self):
        config.ADMIN_PASSWORD = ""
        config.ADMIN_TOTP = ""
        config.ADMIN_TOKEN = ""

    def test_default_deny_without_any_credential(self):
        """什么都没配时不能放行：默认部署不该是「任何人可读全库」。"""
        config.ADMIN_PASSWORD = ""
        config.ADMIN_TOTP = ""
        old = config.DEMO_OPEN
        config.DEMO_OPEN = False
        try:
            r = self.client.get("/api/v1/overview")
        finally:
            config.DEMO_OPEN = old
        self.assertEqual(r.status_code, 403)

    def test_demo_open_allows_local_use(self):
        config.ADMIN_PASSWORD = ""
        config.ADMIN_TOTP = ""
        old = config.DEMO_OPEN
        config.DEMO_OPEN = True
        try:
            r = self.client.get("/api/v1/overview")
        finally:
            config.DEMO_OPEN = old
        self.assertNotIn(r.status_code, (401, 403))

    def test_protected_without_session(self):
        r = self.client.get("/api/v1/overview")
        self.assertEqual(r.status_code, 401)

    def test_login_then_access(self):
        import time
        code = auth.totp(SECRET, at=int(time.time()))
        r = self.client.post("/api/v1/auth/login", json={"password": "correct horse", "code": code})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(auth.COOKIE, r.cookies)
        r = self.client.get("/api/v1/overview")
        self.assertIn(r.status_code, (200, 500))  # 有数据才 200；这里只验鉴权通过
        self.assertNotEqual(r.status_code, 401)

    def test_wrong_password(self):
        r = self.client.post("/api/v1/auth/login", json={"password": "x", "code": "000000"})
        self.assertEqual(r.status_code, 401)

    def test_password_only_is_rejected(self):
        """文档 §6.5：管理员账号 + TOTP；只配口令时拒绝登录。"""
        auth.reset_lockout()
        config.ADMIN_TOTP = ""
        try:
            r = self.client.post("/api/v1/auth/login",
                                 json={"password": "correct horse", "code": ""})
        finally:
            auth.reset_lockout()
        self.assertEqual(r.status_code, 401)
        self.assertIn("TOTP", r.json()["detail"])

    def test_lockout_after_repeated_failures(self):
        auth.reset_lockout()
        for _ in range(auth.FAIL_MAX):
            self.client.post("/api/v1/auth/login", json={"password": "x", "code": "000000"})
        r = self.client.post("/api/v1/auth/login",
                             json={"password": "correct horse", "code": "000000"})
        self.assertEqual(r.status_code, 429, r.text)
        auth.reset_lockout()

    def test_same_totp_code_cannot_be_replayed(self):
        auth.reset_lockout()
        import time
        code = auth.totp(SECRET, at=int(time.time()))
        body = {"password": "correct horse", "code": code}
        first = self.client.post("/api/v1/auth/login", json=body)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post("/api/v1/auth/login", json=body)
        self.assertEqual(second.status_code, 401, second.text)
        self.assertIn("已用过", second.json()["detail"])

    def test_lockout_is_per_ip(self):
        auth.reset_lockout()
        try:
            for _ in range(auth.FAIL_MAX):
                self.client.post("/api/v1/auth/login", json={"password": "x", "code": "000000"},
                                 headers={"x-forwarded-for": "10.1.1.1"})
            blocked = self.client.post("/api/v1/auth/login",
                                       json={"password": "x", "code": "000000"},
                                       headers={"x-forwarded-for": "10.1.1.1"})
            self.assertEqual(blocked.status_code, 429, blocked.text)

            import time
            code = auth.totp(SECRET, at=int(time.time()))
            other = self.client.post("/api/v1/auth/login",
                                     json={"password": "correct horse", "code": code},
                                     headers={"x-forwarded-for": "10.2.2.2"})
            self.assertEqual(other.status_code, 200, other.text)
        finally:
            auth.reset_lockout()

    def test_session_cookie_is_secure_behind_proxy(self):
        """容器里 scheme 恒为 http，Secure 要靠 X-Forwarded-Proto。"""
        auth.reset_lockout()
        import time
        code = auth.totp(SECRET, at=int(time.time()))
        r = self.client.post("/api/v1/auth/login",
                             json={"password": "correct horse", "code": code},
                             headers={"x-forwarded-proto": "https"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Secure", r.headers["set-cookie"])

    def test_wrong_totp(self):
        r = self.client.post("/api/v1/auth/login",
                             json={"password": "correct horse", "code": "000000"})
        self.assertEqual(r.status_code, 401)

    def test_health_is_public(self):
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)

    def test_state_reports_requirement(self):
        self.client.cookies.clear()
        r = self.client.get("/api/v1/auth/state").json()
        self.assertTrue(r["required"])
        self.assertTrue(r["totp"])
        self.assertFalse(r["authed"])


if __name__ == "__main__":
    unittest.main()
