"""GPU 自检：不锁厂商，NVIDIA 与 AMD 都要能识别。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CLOUD_DATA_DIR", tempfile.mkdtemp(prefix="cloud-gpu-"))

from cloud import gpu  # noqa: E402


class GpuTest(unittest.TestCase):
    def test_nvidia(self):
        with mock.patch.object(gpu.shutil, "which",
                               side_effect=lambda n: "/usr/bin/nvidia-smi" if n == "nvidia-smi" else None), \
             mock.patch.object(gpu, "_run", return_value="NVIDIA P104-100"):
            out = gpu.status()
        self.assertEqual(out["vendor"], "nvidia")
        self.assertEqual(out["api"], "cuda")
        self.assertTrue(out["ok"])

    def test_amd(self):
        with mock.patch.object(gpu.shutil, "which",
                               side_effect=lambda n: "/usr/bin/rocm-smi" if n == "rocm-smi" else None), \
             mock.patch.object(gpu, "_run", return_value="AMD Radeon RX 7900"):
            out = gpu.status()
        self.assertEqual(out["vendor"], "amd")
        self.assertEqual(out["api"], "rocm")
        self.assertTrue(out["ok"])

    def test_none(self):
        with mock.patch.object(gpu.shutil, "which", return_value=None), \
             mock.patch.object(gpu.Path, "exists", return_value=False):
            out = gpu.status()
        self.assertEqual(out["vendor"], "none")
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
