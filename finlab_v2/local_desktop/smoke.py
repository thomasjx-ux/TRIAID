"""Fast, offline checks for local-only bootstrap, credentials and preserved UI."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .config import admin_secret, data_dir, ping_proof, session_file, session_secret, valid_session

HERE = Path(__file__).parent
FINLAB = HERE.parent


class LocalDesktopSmoke(unittest.TestCase):
    def test_session_is_private_and_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"TRIAID_LOCAL_HOME": tmp}):
                first = session_secret()
                self.assertEqual(first, session_secret())
                self.assertTrue(valid_session(first))
                self.assertFalse(valid_session("incorrect"))
                self.assertNotEqual(first, admin_secret())
                self.assertNotEqual(first, ping_proof())
                self.assertTrue(session_file().exists())
                if os.name != "nt":
                    self.assertEqual(stat.S_IMODE(session_file().stat().st_mode), 0o600)

    def test_server_never_uses_cloud_persistence(self):
        from .server import configure

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "TRIAID_LOCAL_HOME": tmp,
                "TRIAID_STORAGE_BACKEND": "supabase",
                "TRIAID_SUPABASE_TOKEN": "test-cloud-key",
                "TRIAID_SUPABASE_PERSISTENCE_URL": "https://example.invalid",
            }):
                configure()
                self.assertEqual(os.environ["TRIAID_STORAGE_BACKEND"], "file")
                self.assertEqual(os.environ["TRIAID_DATA_DIR"], str(data_dir()))
                self.assertNotIn("TRIAID_SUPABASE_TOKEN", os.environ)
                self.assertNotIn("TRIAID_SUPABASE_PERSISTENCE_URL", os.environ)
                self.assertEqual(os.environ["TRIAID_LOCAL_DESKTOP_MODE"], "1")

    def test_disk_probe_persists_across_independent_python_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "TRIAID_LOCAL_HOME": tmp}
            first = subprocess.run(
                [sys.executable, "-m", "local_desktop.durability"],
                cwd=str(FINLAB), env=env, text=True,
                capture_output=True, check=True,
            )
            second = subprocess.run(
                [sys.executable, "-m", "local_desktop.durability"],
                cwd=str(FINLAB), env=env, text=True,
                capture_output=True, check=True,
            )
            a = json.loads(first.stdout)
            b = json.loads(second.stdout)
            self.assertEqual(a["status"], "PENDING_SECOND_START")
            self.assertEqual(b["status"], "VERIFIED_AFTER_RESTART")
            self.assertFalse(b["power_loss_recovery_tested"])
            self.assertFalse(b["independent_backup_tested"])

    def test_local_file_backend_survives_reopen(self):
        from .durability import verify
        from triaid_fin.storage_backend import FileStorageBackend
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            verify(root, "boot-one")
            proof = verify(root, "boot-two")
            with patch.dict(os.environ, {
                "TRIAID_LOCAL_DESKTOP_MODE": "1",
                "TRIAID_LOCAL_STORAGE_RESTART_VERIFIED": "1",
                "TRIAID_LOCAL_STORAGE_PROBE_ROOT": proof["root"],
                "TRIAID_LOCAL_STORAGE_PROBE_STATUS": proof["status"],
            }):
                store = FileStorageBackend(str(root))
                self.assertTrue(store.persistent)
                store.atomic_write_text("durability-test.json", '{"ok":true}')
                store.append_line("durability-test.jsonl", '{"event":1}')
                restored = FileStorageBackend(str(root))
                self.assertEqual(restored.read_text("durability-test.json"), '{"ok":true}')
                self.assertEqual(restored.read_lines("durability-test.jsonl"), ['{"event":1}'])

    def test_full_original_ui_is_embedded_not_reimplemented(self):
        html = (HERE / "ui" / "shell.html").read_text(encoding="utf-8")
        self.assertIn('<iframe id="classic" src="/"', html)
        self.assertIn('id="live"', html)
        self.assertIn('data-view="classic"', html)
        self.assertIn('data-view="live"', html)

    def test_real_time_sources_are_research_records(self):
        html = (HERE / "ui" / "live.html").read_text(encoding="utf-8")
        self.assertIn("/api/market-data/live-indicators/", html)
        self.assertIn("/api/decision-scheduler/events", html)
        self.assertIn("/api/market-data/observations", html)
        self.assertIn("source_latest_ts", html)
        self.assertNotIn("Math.random(", html)
        self.assertIn('new EventSource("/desktop/events")', html)

    def test_loopback_only_and_separate_backend_process(self):
        server = (HERE / "server.py").read_text(encoding="utf-8")
        client = (HERE / "client.py").read_text(encoding="utf-8")
        auth = (HERE / "app.py").read_text(encoding="utf-8")
        self.assertIn("host=HOST", server)
        self.assertIn("workers=1", server)
        self.assertIn("DETACHED_PROCESS", client)
        self.assertIn("desktop_security", auth)
        self.assertIn('httponly=True, samesite="strict"', auth)
        self.assertIn("FOREIGN_ORIGIN_REJECTED", auth)


if __name__ == "__main__":
    unittest.main()
