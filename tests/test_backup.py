"""备份：加密副本不能和明文躺在一起，恢复演练只能靠加密副本也能对账。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="cloud-backup-")
os.environ.setdefault("CLOUD_DATA_DIR", _TMP)
os.environ.setdefault("CLOUD_AI_MODE", "rules")

from tests.support import ensure_pg  # noqa: E402

ensure_pg()

from cloud import config, jobs, store  # noqa: E402


def _pg_bin() -> str:
    """pg_dump / pg_restore 只在 pgserver 的 pginstall 里，测试时借它的 PATH。"""
    try:
        import pgserver
        path = Path(pgserver.__file__).parent / "pginstall" / "bin"
        return str(path) if (path / "pg_dump").exists() else ""
    except ImportError:
        return ""


HAS_GPG = bool(shutil.which("gpg"))
PG_BIN = _pg_bin() or (str(Path(shutil.which("pg_dump")).parent) if shutil.which("pg_dump") else "")


class _BackupDirMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cloud-backup-dir-"))
        self.old = (config.BACKUP_DIR, config.BACKUP_KEY_FILE)
        key = self.tmp / "backup.key"
        key.write_text("test-passphrase\n", encoding="utf-8")
        config.BACKUP_DIR = self.tmp
        config.BACKUP_KEY_FILE = key

    def tearDown(self):
        config.BACKUP_DIR, config.BACKUP_KEY_FILE = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)


@unittest.skipUnless(HAS_GPG, "没有 gpg")
class EncryptTest(_BackupDirMixin, unittest.TestCase):
    def test_encrypt_removes_the_plaintext_dump(self):
        store.init_db()
        dump = self.tmp / "cloud-20261115-030000.dump"
        dump.write_bytes(b"not really a pg dump")
        out = jobs._encrypt(dump)
        self.assertTrue(out["ok"], out)
        self.assertFalse(dump.exists(), "加密副本同目录，明文必须删掉")
        self.assertTrue((self.tmp / "cloud-20261115-030000.dump.gpg").exists())
        self.assertEqual(out["file"], "cloud-20261115-030000.dump.gpg")


@unittest.skipUnless(HAS_GPG and PG_BIN, "没有 gpg 或 pg 工具")
class DrillTest(_BackupDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = PG_BIN + os.pathsep + self.old_path
        store.init_db()

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        super().tearDown()

    def test_drill_works_with_only_the_encrypted_copy(self):
        # 备份里得有数据，对账才有意义（单跑这个文件时库是空的）
        with store.connect() as conn:
            conn.execute("INSERT INTO edges(edge_id, name, last_seen) VALUES('edge-drill','演练',%s)"
                         " ON CONFLICT (edge_id) DO NOTHING", (store.now_ms(),))
        backed = jobs.run_backup()
        self.assertTrue(backed["ok"], backed)
        dump = self.tmp / backed["file"]
        enc = jobs._encrypt(dump)
        self.assertTrue(enc["ok"], enc)
        self.assertFalse(dump.exists())

        out = jobs.run_restore_drill()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["file"], enc["file"], "演练应该用加密副本，而不是明文")
        self.assertGreater(out["counts"]["edges"], 0)


if __name__ == "__main__":
    unittest.main()
