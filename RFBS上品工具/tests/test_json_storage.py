import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import atomic_write_json, load_json
from app import RfbsListingApp


class JsonStorageTests(unittest.TestCase):
    def test_temporary_windows_denial_retries_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto_jobs.json"
            atomic_write_json(path, {"stage": 2})
            replace = os.replace
            error = PermissionError("temporarily held open")
            error.winerror = 5
            calls = []
            def flaky(source, target):
                calls.append(source)
                if len(calls) < 3:
                    self.assertEqual(load_json(path), {"stage": 2})
                    raise error
                replace(source, target)
            with patch("core.os.replace", side_effect=flaky), patch("core.time.sleep"):
                atomic_write_json(path, {"stage": 3, "task_id": 456})
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(set(calls)), 1)
            self.assertEqual(load_json(path), {"stage": 3, "task_id": 456})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_persistent_denial_preserves_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto_jobs.json"
            atomic_write_json(path, {"task_id": 123})
            before = path.read_bytes()
            with patch("core.os.replace", side_effect=PermissionError("denied")) as replace, patch("core.time.sleep"):
                with self.assertRaises(PermissionError):
                    atomic_write_json(path, {"task_id": 456})
            self.assertEqual(replace.call_count, 20)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_unrelated_replace_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("core.os.replace", side_effect=OSError("disk full")) as replace, patch("core.time.sleep") as sleep:
                with self.assertRaises(OSError):
                    atomic_write_json(Path(directory) / "jobs.json", {})
            replace.assert_called_once()
            sleep.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows sharing test")
    def test_legacy_open_reader_can_release_lock_while_writer_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto_jobs.json"
            atomic_write_json(path, {"stage": 2})
            reader = path.open("r", encoding="utf-8")
            errors = []
            def write():
                try:
                    atomic_write_json(path, {"stage": 3})
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=write)
            worker.start()
            try:
                time.sleep(0.12)
                self.assertEqual(json.load(reader), {"stage": 2})
            finally:
                reader.close()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(load_json(path), {"stage": 3})

    def test_concurrent_snapshot_readers_never_see_missing_or_partial_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto_jobs.json"
            atomic_write_json(path, {"stage": 0, "data": list(range(5000))})
            errors = []
            stop = threading.Event()
            def read():
                while not stop.is_set():
                    snapshot = load_json(path, "failed")
                    if not isinstance(snapshot, dict) or len(snapshot.get("data", [])) != 5000:
                        errors.append(snapshot)
                        break
            readers = [threading.Thread(target=read) for _ in range(3)]
            for reader in readers:
                reader.start()
            try:
                for stage in range(10):
                    atomic_write_json(path, {"stage": stage, "data": list(range(5000))})
            finally:
                stop.set()
                for reader in readers:
                    reader.join(5)
            self.assertEqual(errors, [])
            self.assertEqual(load_json(path)["stage"], 9)

    def test_unreadable_existing_queue_is_never_replaced_by_empty_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto_jobs.json"
            path.write_text("corrupt JSON", encoding="utf-8")
            app = RfbsListingApp.__new__(RfbsListingApp)
            with patch("app.AUTO_JOBS_PATH", path), patch.object(app, "_save_auto_jobs") as save:
                with self.assertRaisesRegex(OSError, "原文件已保留"):
                    app._load_auto_jobs()
            save.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), "corrupt JSON")


if __name__ == "__main__":
    unittest.main()
