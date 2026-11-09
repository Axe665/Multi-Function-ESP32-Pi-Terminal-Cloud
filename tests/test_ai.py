"""AI 报告：契约、模板兜底、24 小时去重。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="cloud-ai-")
os.environ.setdefault("CLOUD_DB", os.path.join(_TMP, "cloud.db"))
os.environ.setdefault("CLOUD_DATA_DIR", _TMP)
os.environ.setdefault("CLOUD_AI_MODE", "rules")

from cloud import ai, models, store  # noqa: E402


class AiReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()
        from cloud import seed
        seed.generate(7)

    def test_rules_report_validates(self):
        out = ai.generate("fleet", None, force_rules=True)
        content = models.AiReportContent.model_validate(out["content"])
        self.assertTrue(content.headline)
        self.assertTrue(content.narrative)
        self.assertEqual(content.scope, "fleet")
        self.assertEqual(out["model"], "rules-v1")

    def test_second_call_is_cached(self):
        first = ai.generate("edge", "edge-lab-01", force_rules=True)
        second = ai.generate("edge", "edge-lab-01")
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second.get("cached"))

    def test_report_needs_data(self):
        # 一个没有数据的设备也必须有报告，不允许返回空
        out = ai.generate("device", "no-such-device", force_rules=True)
        self.assertTrue(out["content"]["narrative"])
        self.assertEqual(out["content"]["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
