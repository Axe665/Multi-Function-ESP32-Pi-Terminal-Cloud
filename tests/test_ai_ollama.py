"""模式 B：本地 Ollama 的请求/响应契约，以及不可达时的规则兜底。"""
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
_TMP = tempfile.mkdtemp(prefix="cloud-ai-ollama-")
os.environ.setdefault("CLOUD_DATA_DIR", _TMP)

from tests.support import ensure_pg  # noqa: E402

ensure_pg()

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
    digest = "sha256:" + "a" * 64

    def do_GET(self):  # noqa: N802 — Ollama /api/tags
        raw = json.dumps({"models": [{"name": "stub-7b", "model": "stub-7b",
                                      "digest": type(self).digest}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        type(self).calls += 1
        raw = json.dumps({"response": json.dumps(REPORT, ensure_ascii=False)}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class OllamaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()
        seed.generate(5)
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.old = (config.AI_MODE, config.OLLAMA_URL, config.OLLAMA_MODEL)
        config.AI_MODE = "local"
        config.OLLAMA_URL = f"http://127.0.0.1:{cls.server.server_port}"
        config.OLLAMA_MODEL = "stub-7b"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        config.AI_MODE, config.OLLAMA_URL, config.OLLAMA_MODEL = cls.old

    def test_local_model_path_and_task(self):
        out = ai.generate("edge", "edge-lab-01", task="fleet_compare", period=("2026-11-01", "2026-11-09"))
        self.assertEqual(out["model"], "stub-7b", out)
        self.assertEqual(out["content"]["task"], "fleet_compare")
        self.assertEqual(out["content"]["period"]["from"], "2026-11-01")
        self.assertGreaterEqual(_Handler.calls, 1)

    def test_unreachable_falls_back_to_rules(self):
        old = config.OLLAMA_URL
        config.OLLAMA_URL = "http://127.0.0.1:1"
        try:
            out = ai.generate("device", "no-such-device", task="advice_digest")
        finally:
            config.OLLAMA_URL = old
        self.assertEqual(out["model"], "rules-v1")
        self.assertTrue(out["content"]["narrative"])

    def test_model_digest_recorded_in_manifest(self):
        """文档 §6.8：首次看到模型就把 SHA256 记进清单，之后靠它比对。"""
        path = config.MODEL_MANIFEST
        path.unlink(missing_ok=True)
        ai._model_cache.update(at=0.0, value=None)
        first = ai.model_status()
        self.assertTrue(first["reachable"], first)
        self.assertEqual(first["digest"], _Handler.digest)
        self.assertEqual(first["expected_source"], "manifest")
        self.assertTrue(path.exists())
        ai._model_cache.update(at=0.0, value=None)
        second = ai.model_status()
        self.assertTrue(second["match"])
        path.unlink(missing_ok=True)
        ai._model_cache.update(at=0.0, value=None)
