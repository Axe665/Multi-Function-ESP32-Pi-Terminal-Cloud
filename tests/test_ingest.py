"""摄取边界：幂等、原始字段拒收、k<5 拒收。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="cloud-test-")
os.environ.setdefault("CLOUD_DATA_DIR", _TMP)
os.environ.setdefault("CLOUD_AI_MODE", "rules")

from fastapi.testclient import TestClient  # noqa: E402

from tests.support import ensure_pg  # noqa: E402

ensure_pg()

from cloud import store  # noqa: E402
from cloud.main import app  # noqa: E402

EXAMPLE = ROOT / "contracts" / "vendor" / "edge" / "edge-report@v1.example.json"


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


class IngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()
        cls.client = TestClient(app)

    def test_accept_then_dedupe(self):
        payload = example()
        first = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["duplicate"])

        second = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(second.json()["rows"], 0)

    def test_raw_field_rejected(self):
        payload = example()
        payload["report_id"] = "raw-test-0001"
        payload["telemetry_daily"][0]["subject"] = "abc123"
        r = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["code"], "RAW_PAYLOAD_REJECTED")
        self.assertGreaterEqual(store.counters().get("raw_rejected", 0), 1)

    def test_personalization_flag_accepted(self):
        payload = example()
        payload["report_id"] = "consent-off-0001"
        payload["privacy"]["personalization"] = False
        r = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["accepted"])

    def test_low_k_rejected(self):
        payload = example()
        payload["report_id"] = "lowk-test-0001"
        payload["telemetry_daily"][0]["k"] = 4
        r = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(r.status_code, 422)

    def test_rows_land_in_daily_table(self):
        payload = example()
        payload["report_id"] = "land-test-0001"
        self.client.post("/api/v1/ingest/edge-report", json=payload)
        rows = store.daily_rows(500, payload["edge_id"])
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["k"] >= 5 for r in rows))


class AuditTest(unittest.TestCase):
    def test_audit_counts_match_rows(self):
        store.init_db()
        from cloud import seed
        seed.generate(5)
        a = TestClient(app).get("/api/v1/privacy/audit").json()
        self.assertEqual(sum(a["k_distribution"].values()), a["rows"])
        self.assertGreater(a["rows"], 0)
        self.assertGreaterEqual(a["k_min_seen"], 5)


if __name__ == "__main__":
    unittest.main()
