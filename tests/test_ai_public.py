"""AI：公有 API 模式、预算熔断、规则兜底。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="cloud-ai-pub-")
os.environ.setdefault("CLOUD_DB", os.path.join(_TMP, "cloud.db"))
os.environ.setdefault("CLOUD_DATA_DIR", _TMP)

from cloud import ai, config, seed, store  # noqa: E402

REPORT = {
    "schema": "ai.report.v1",
    "headline": "温度本周期上升 1.2 °C",
    "narrative": "温室边节点的温度在周期后段抬升，与午后通风不足的时间吻合。",
    "findings": [{"label": "温度", "detail": "均值 24.1 °C", "risk": "watch"}],
    "actions": ["检查午后通风"],
    "confidence": "high",
}


class _Handler(BaseHTTPRequestHandler):
    calls = 0
    fail = False
    body: dict = {}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        type(self).body = json.loads(self.rfile.read(length))
        type(self).calls += 1
        if type(self).fail:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"nope")
            return
        payload = {
            "choices": [{"message": {"content": json.dumps(REPORT, ensure_ascii=False)}}],
            "usage": {"total_tokens": 321},
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class PublicApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()
        seed.generate(5)
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        config.PUBLIC_API_URL = f"http://127.0.0.1:{cls.server.server_port}/v1/chat/completions"
        config.PUBLIC_API_KEY = "test-key"
        config.PUBLIC_MODEL = "qwen3.5"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.calls = 0
        _Handler.fail = False
        with store.connect() as conn:
            conn.execute("DELETE FROM ai_reports")
            conn.execute("DELETE FROM counters")

    def test_api_mode_uses_public_model(self):
        config.AI_MODE = "api"
        out = ai.generate("fleet", None)
        self.assertEqual(out["model"], "qwen3.5")
        self.assertEqual(out["content"]["headline"], REPORT["headline"])
        self.assertGreaterEqual(store.counters().get("api_tokens", 0), 321)
        # 只发聚合摘要，不发任何原始字段
        body = json.dumps(_Handler.body, ensure_ascii=False)
        for forbidden in ("user_id", "subject", "values"):
            self.assertNotIn(forbidden, body)
        self.assertEqual(_Handler.body["model"], "qwen3.5")

    def test_budget_exhausted_falls_back_to_rules(self):
        config.AI_MODE = "api"
        store.bump("api_tokens", config.API_BUDGET_TOKENS + 1)
        out = ai.generate("fleet", None)
        self.assertEqual(out["model"], "rules-v1")
        self.assertEqual(_Handler.calls, 0)
        self.assertTrue(out["content"]["narrative"])

    def test_api_failure_falls_back_to_rules(self):
        config.AI_MODE = "api"
        _Handler.fail = True
        out = ai.generate("fleet", None)
        self.assertEqual(out["model"], "rules-v1")
        self.assertEqual(_Handler.calls, 2)  # 重试一次后放弃

    def test_rules_mode_never_calls_out(self):
        config.AI_MODE = "rules"
        out = ai.generate("fleet", None)
        self.assertEqual(out["model"], "rules-v1")
        self.assertEqual(_Handler.calls, 0)


if __name__ == "__main__":
    unittest.main()
