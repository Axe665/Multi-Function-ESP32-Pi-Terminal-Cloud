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
os.environ.setdefault("CLOUD_SEED_FORCE", "1")  # 用例会重写演示库

from fastapi.testclient import TestClient  # noqa: E402

from tests.support import ensure_pg  # noqa: E402

ensure_pg()

from cloud import config, store  # noqa: E402
from cloud.main import app  # noqa: E402

# 没配凭据时接口默认 403（防止「没配」被当成「不用鉴权」）。
# 这个文件里的用例按本机演示跑；门禁用例自己在 try/finally 里关掉再验。
config.DEMO_OPEN = True

EXAMPLE = ROOT / "contracts" / "vendor" / "edge" / "edge-report@v1.example.json"


def example() -> dict:
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    # 示例契约里没有 group_analysis（默认关）；用例测的是「边明确同意」的路径
    payload["privacy"]["group_analysis"] = True
    return payload


class IngestGuardTest(unittest.TestCase):
    """没凭据时默认拒绝；有证书 CN 时必须与 edge_id 一致。"""

    @classmethod
    def setUpClass(cls):
        store.init_db()
        cls.client = TestClient(app)

    def test_rejects_without_credentials(self):
        from cloud import config
        old = config.DEMO_OPEN
        config.DEMO_OPEN = False
        try:
            r = self.client.post("/api/v1/ingest/edge-report", json=example())
        finally:
            config.DEMO_OPEN = old
        self.assertEqual(r.status_code, 403)

    def test_cert_cn_must_match_edge_id(self):
        r = self.client.post("/api/v1/ingest/edge-report", json=example(),
                             headers={"x-client-cn": "CN=some-other-edge,OU=mt-edge"})
        self.assertEqual(r.status_code, 403)

    def test_cert_cn_match_is_accepted(self):
        payload = example()
        payload["report_id"] = "cn-match-0001"  # 别和去重用例撞同一份
        r = self.client.post("/api/v1/ingest/edge-report", json=payload,
                             headers={"x-client-cn": f"CN={payload['edge_id']},OU=mt-edge"})
        self.assertEqual(r.status_code, 200, r.text)


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
        self.assertGreaterEqual(store.counters().get("raw_rejected_local", 0), 1)

    def test_min_max_rejected(self):
        # 文档 §8.2：出端前必须去掉 min/max
        payload = example()
        payload["report_id"] = "raw-test-0002"
        payload["telemetry_daily"][0]["min"] = 1.0
        payload["telemetry_daily"][0]["max"] = 9.0
        r = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["code"], "RAW_PAYLOAD_REJECTED")
        self.assertTrue(any(f.endswith(".min") for f in r.json()["detail"]["fields"]))

    def test_personalization_flag_accepted(self):
        payload = example()
        payload["report_id"] = "consent-off-0001"
        payload["privacy"]["personalization"] = False
        r = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["accepted"])

    def test_group_analysis_flag_gates_data(self):
        payload = example()
        payload["edge_id"] = "edge-consent-test"
        payload["report_id"] = "consent-off-0002"
        payload["privacy"]["group_analysis"] = False
        for d in payload["devices"]:
            d["device_id"] = "dev-consent"
        for row in payload["telemetry_daily"]:
            row["device_id"] = "dev-consent"
            row["metric"] = "consent_metric"
        for a in payload["advice"]:
            a["device_id"] = "dev-consent"
        r = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(r.status_code, 200, r.text)

        self.assertEqual(store.series("edge-consent-test", None, "consent_metric"), [])
        self.assertEqual(store.daily_rows(10, "edge-consent-test"), [])
        self.assertEqual(store.advice("edge-consent-test"), [])
        edge = next(e for e in store.edges() if e["edge_id"] == "edge-consent-test")
        self.assertFalse(edge["group_analysis"])

        payload["report_id"] = "consent-on-0001"
        payload["privacy"]["group_analysis"] = True
        self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertGreater(len(store.series("edge-consent-test", None, "consent_metric")), 0)

    def test_low_k_row_is_dropped_not_the_whole_report(self):
        # 与边一致按行丢：一条 k=4 不该把同批 k=8 的行一起打回
        payload = example()
        payload["report_id"] = "lowk-test-0002"
        payload["edge_id"] = "edge-lowk-01"
        low, keep = payload["telemetry_daily"][0], payload["telemetry_daily"][1]
        low["k"] = 4
        low["metric"] = "lowk_dropped"
        keep["k"] = 8
        keep["metric"] = "lowk_kept"
        payload["telemetry_daily"] = [low, keep]

        before = store.counters().get("suppressed_buckets", 0)
        r = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rows"], 1)

        rows = store.daily_rows(200, "edge-lowk-01")
        self.assertEqual([x["metric"] for x in rows], ["lowk_kept"])
        self.assertEqual(store.counters().get("suppressed_buckets", 0), before + 1)
        self.assertIn("ingest.suppressed", {a["action"] for a in store.audit_log(20)})

    def test_missing_group_analysis_means_off(self):
        # 文档 §8.3：缺字段按「没同意」处理，老边不带就不参与群体分析
        payload = example()
        payload["report_id"] = "consent-default-0001"
        payload["edge_id"] = "edge-no-consent"
        payload["privacy"].pop("group_analysis")
        r = self.client.post("/api/v1/ingest/edge-report", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        edge = next(e for e in store.edges() if e["edge_id"] == "edge-no-consent")
        self.assertFalse(edge["group_analysis"])
        self.assertEqual(store.daily_rows(10, "edge-no-consent"), [])

    def test_edge_version_is_recorded(self):
        # 文档 §6.2：edges.version 记边版本，缺字段写 unknown
        payload = example()
        payload["report_id"] = "ver-test-0001"
        payload["edge_id"] = "edge-ver-01"
        payload["edge_version"] = "2.3.1"
        self.assertEqual(self.client.post("/api/v1/ingest/edge-report", json=payload).status_code, 200)
        edge = next(e for e in store.edges() if e["edge_id"] == "edge-ver-01")
        self.assertEqual(edge["version"], "2.3.1")

        payload["report_id"] = "ver-test-0002"
        payload["edge_id"] = "edge-ver-02"
        payload.pop("edge_version")
        self.assertEqual(self.client.post("/api/v1/ingest/edge-report", json=payload).status_code, 200)
        edge = next(e for e in store.edges() if e["edge_id"] == "edge-ver-02")
        self.assertEqual(edge["version"], "unknown")

    def test_rows_land_in_daily_table(self):
        payload = example()
        payload["report_id"] = "land-test-0001"
        self.client.post("/api/v1/ingest/edge-report", json=payload)
        rows = store.daily_rows(500, payload["edge_id"])
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["k"] >= 5 for r in rows))


class CompareTest(unittest.TestCase):
    def test_compare_edges_and_devices(self):
        store.init_db()
        from cloud import seed
        seed.generate(3)
        c = TestClient(app)
        metric = c.get("/api/v1/meta").json()["metrics"][0]["key"]

        edges = c.get(f"/api/v1/compare?metric={metric}").json()
        self.assertEqual(edges["scope"], "edges")
        self.assertGreater(len(edges["edges"]), 0)

        devices = c.get(f"/api/v1/compare?metric={metric}&scope=devices").json()
        self.assertEqual(devices["scope"], "devices")
        self.assertGreater(len(devices["devices"]), 0)
        self.assertTrue(all(d["device_id"] for d in devices["devices"]))

    def test_health_reports_gpu_and_model(self):
        c = TestClient(app)
        h = c.get("/api/v1/health").json()
        self.assertIn("gpu", h)
        self.assertIn("model", h)


class AuditTest(unittest.TestCase):
    def test_audit_counts_match_rows(self):
        store.init_db()
        from cloud import seed
        seed.generate(5)
        a = TestClient(app).get("/api/v1/privacy/audit").json()
        self.assertEqual(sum(a["k_distribution"].values()), a["rows"])
        self.assertGreater(a["rows"], 0)
        self.assertGreaterEqual(a["k_min_seen"], 5)


class SystemAlertTest(unittest.TestCase):
    """文档 §6.2：原始字段上报不只计数，还要在总览里告警。"""

    def test_raw_payload_rejection_raises_alert(self):
        store.init_db()
        c = TestClient(app)
        payload = example()
        payload["report_id"] = "raw-alert-0001"
        payload["telemetry_daily"][0]["subject"] = "abc123"
        self.assertEqual(c.post("/api/v1/ingest/edge-report", json=payload).status_code, 422)

        kinds = [a["kind"] for a in c.get("/api/v1/overview").json()["alerts"]]
        self.assertIn("raw_payload", kinds)


if __name__ == "__main__":
    unittest.main()
