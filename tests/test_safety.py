"""数据安全的护栏：心跳只碰演示边、seed 不清有数据的库、预算按月重置。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="cloud-safety-")
os.environ.setdefault("CLOUD_DATA_DIR", _TMP)
os.environ.setdefault("CLOUD_AI_MODE", "rules")

from tests.support import ensure_pg  # noqa: E402

ensure_pg()

from cloud import ai, config, jobs, seed, store  # noqa: E402


def _ms(t: float) -> int:
    return int(t * 1000)


class HeartbeatTest(unittest.TestCase):
    """演示心跳不能替真实边续命，否则掉线告警永远不触发。"""

    def setUp(self):
        store.init_db()
        with store.connect() as conn:
            conn.execute("DELETE FROM devices")   # 先清有外键的一侧
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM reports")
        stale = _ms(time.time() - 6 * 3600)
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO edges(edge_id, name, last_seen, version, group_analysis, is_demo)"
                " VALUES(%s,%s,%s,%s,TRUE,FALSE)", ("edge-real-01", "真边", stale, "1.0.0"))
            conn.execute(
                "INSERT INTO edges(edge_id, name, last_seen, version, group_analysis, is_demo)"
                " VALUES(%s,%s,%s,%s,TRUE,TRUE)", ("edge-demo-01", "演示边", stale, "1.0.0"))

    def test_only_demo_edges_get_heartbeat(self):
        old_demo = config.DEMO
        config.DEMO = True
        try:
            jobs.demo_heartbeat()
        finally:
            config.DEMO = old_demo
        with store.connect() as conn:
            rows = {r["edge_id"]: r["last_seen"] for r in conn.execute(
                "SELECT edge_id, last_seen FROM edges")}
        stale = _ms(time.time() - 6 * 3600)
        self.assertLessEqual(rows["edge-real-01"], stale + 1000, "真实边的 last_seen 不许被动过")
        self.assertGreater(rows["edge-demo-01"], stale + 1000, "演示边应该被续上")


class SeedGuardTest(unittest.TestCase):
    """库里有数据时，seed 必须拒绝执行——它会先 DELETE 九张表。"""

    def setUp(self):
        store.init_db()
        with store.connect() as conn:
            conn.execute("DELETE FROM devices")   # devices 有外键指过来，先清
            conn.execute("DELETE FROM edges")
            conn.execute("INSERT INTO edges(edge_id, name, last_seen, version) VALUES('e1','x',0,'1')")

    def test_refuses_when_data_exists(self):
        with self.assertRaises(SystemExit) as ctx:
            seed.generate(1)
        self.assertIn("库里已有数据", str(ctx.exception))

    def test_force_flag_allows(self):
        os.environ["CLOUD_SEED_FORCE"] = "1"
        try:
            seed.generate(1)
        finally:
            os.environ.pop("CLOUD_SEED_FORCE", None)
        self.assertGreater(len(store.edges()), 0)


class BudgetTest(unittest.TestCase):
    """额度按自然月算，跨月要重置；同月内照旧。"""

    def setUp(self):
        store.init_db()
        with store.connect() as conn:
            conn.execute("DELETE FROM counters")

    def test_same_month_counts(self):
        store.set_counter("api_tokens", config.API_BUDGET_TOKENS)
        store.set_counter("api_tokens_month", int(time.strftime("%Y%m")))
        self.assertEqual(ai._budget_left(), 0)

    def test_new_month_resets(self):
        store.set_counter("api_tokens", config.API_BUDGET_TOKENS)
        store.set_counter("api_tokens_month", 200001)   # 一个旧月份
        self.assertEqual(ai._budget_left(), config.API_BUDGET_TOKENS)
        self.assertEqual(store.counters().get("api_tokens"), 0)


class KMinConfigTest(unittest.TestCase):
    """k 门槛跟配置走，不能写死 5。"""

    def _report(self) -> dict:
        import json
        path = ROOT / "contracts/vendor/edge/edge-report@v1.example.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        for row in report["telemetry_daily"]:
            row["k"] = 5
            row["n_subjects"] = max(row.get("n_subjects", 5), 5)
        return report

    def test_k_below_configured_min_is_rejected(self):
        from cloud import models
        old = config.K_MIN
        config.K_MIN = 10
        try:
            with self.assertRaises(Exception):
                models.EdgeReport.model_validate(self._report())
        finally:
            config.K_MIN = old
        models.EdgeReport.model_validate(self._report())   # 默认门槛下这份能过


class PublicPayloadTest(unittest.TestCase):
    """模式 A 只能拿到 L2：建议原文与词云词属于 L3，不许出网。"""

    def test_public_view_strips_l3(self):
        summary = {
            "scope": "fleet", "scope_id": None, "from": "2026-11-01", "to": "2026-11-14",
            "devices": 9,
            "metrics": [{"metric": "temperature_c", "k_min": 6}, {"metric": "x", "k_min": 2}],
            "advice_top": [{"text": "王小明家湿度低"}],
            "advice_counts": [{"text": "某句建议"}],
            "words": ["卧室", "奶奶"],
        }
        view = ai._public_view(summary)
        self.assertNotIn("advice_top", view)
        self.assertNotIn("advice_counts", view)
        self.assertNotIn("words", view)
        self.assertEqual(len(view["metrics"]), 1, "k<5 的指标不能出网")
        self.assertEqual(view["metrics"][0]["k_min"], 6)


if __name__ == "__main__":
    unittest.main()
