"""云端告警：日聚合 z 的连续判定、确认与静音。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="cloud-alerts-")
os.environ.setdefault("CLOUD_DATA_DIR", _TMP)
os.environ.setdefault("CLOUD_AI_MODE", "rules")

from tests.support import ensure_pg  # noqa: E402

ensure_pg()

from cloud import store  # noqa: E402


class CloudAlertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()

    def setUp(self):
        with store.connect() as conn:
            conn.execute("DELETE FROM alert_state")
            conn.execute("DELETE FROM telemetry_daily")
            conn.execute("DELETE FROM audit")
            conn.execute("INSERT INTO edges(edge_id, name, last_seen) VALUES('e1','边一',%s)"
                         " ON CONFLICT (edge_id) DO UPDATE SET last_seen=EXCLUDED.last_seen",
                         (store.now_ms(),))
            conn.execute("INSERT INTO devices(device_id, edge_id, scene, modules_json, last_seen)"
                         " VALUES('d1','e1','实验室','[]',%s)"
                         " ON CONFLICT (device_id) DO UPDATE SET edge_id=EXCLUDED.edge_id",
                         (store.now_ms(),))

    def _day(self, offset, z):
        import datetime as dt
        day = (dt.date.today() - dt.timedelta(days=offset)).isoformat()
        with store.connect() as conn:
            conn.execute(
                """INSERT INTO telemetry_daily
                   (edge_id, device_id, day, metric, n_subjects, n_samples, mean, slope, z, k, label, unit)
                   VALUES('e1','d1',%s,'volt',9,360,3.9,0.1,%s,9,'电压','V')
                   ON CONFLICT (edge_id, device_id, day, metric) DO UPDATE SET z=EXCLUDED.z""",
                (day, z))

    def feed(self):
        return store.alert_feed(days=30)

    def test_consecutive_days_is_critical(self):
        self._day(3, 3.0)
        self._day(2, 2.9)
        rows = [a for a in self.feed() if a["metric"] == "volt"]
        self.assertEqual(len(rows), 1, "连续两日应合并成一条")
        self.assertEqual(rows[0]["severity"], "critical")

    def test_single_day_is_notice(self):
        self._day(1, 2.3)
        rows = [a for a in self.feed() if a["metric"] == "volt"]
        self.assertEqual(rows[0]["severity"], "notice")

    def test_recovery_and_mute(self):
        self._day(4, 2.8)
        self._day(3, 2.8)
        rows = [a for a in self.feed() if a["metric"] == "volt"]
        key = rows[0]["alert_key"]
        store.set_alert_state(key, "admin", acked=True)
        rows = [a for a in self.feed() if a["metric"] == "volt"]
        self.assertEqual(rows[0]["state"], "acked")

        store.set_alert_state(key, "admin", mute_hours=8, reason="检修中")
        rows = [a for a in self.feed() if a["metric"] == "volt"]
        self.assertEqual(rows[0]["state"], "muted")
        self.assertIn("alert.mute", {r["action"] for r in store.audit_log(10)})

        self._day(2, 0.2)
        store.clear_alert_mute(key)
        rows = [a for a in self.feed() if a["metric"] == "volt"]
        self.assertEqual(rows[0]["state"], "resolved")

    def test_stale_edge_is_critical(self):
        with store.connect() as conn:
            conn.execute("UPDATE edges SET last_seen=%s WHERE edge_id='e1'",
                         (store.now_ms() - 3 * 3600_000,))
        stale = [a for a in self.feed() if a["kind"] == "stale"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["severity"], "critical")


if __name__ == "__main__":
    unittest.main()
