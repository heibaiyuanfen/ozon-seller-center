import tempfile
import unittest
from pathlib import Path

from PIL import Image

from codex_images import CodexDesktopImages
from core import atomic_write_json


class CodexImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reference = self.root / "source.png"
        Image.new("RGB", (900, 1200), "red").save(self.reference)
        self.service = CodexDesktopImages(self.root / "tasks", {"ozon_shop_id": "shop1"})

    def complete(self, folder, task_id, size=(900, 1200), status="completed"):
        Image.new("RGB", size, "blue").save(folder / "result.png")
        atomic_write_json(folder / "result.json", {"task_id": task_id, "status": status, "error": "test failure"})

    def test_result_is_received_and_copied(self):
        folder, task_id = self.service.prepare(self.reference, "test")
        self.assertTrue((folder / "Codex任务.txt").is_file())
        self.assertIsNone(self.service.receive(folder, task_id, self.root / "output"))
        self.complete(folder, task_id)
        paths = self.service.generate([str(self.reference)], "test", 1, self.root / "output")
        self.assertTrue(Path(paths[0]).is_file())

    def test_wrong_task_id_cannot_be_received(self):
        folder, task_id = self.service.prepare(self.reference, "test")
        self.complete(folder, "wrong-task")
        self.assertIsNone(self.service.receive(folder, task_id, self.root / "output"))

    def test_each_shop_has_its_own_task(self):
        first, _ = self.service.prepare(self.reference, "test")
        other = CodexDesktopImages(self.root / "tasks", {"ozon_shop_id": "shop2"})
        second, _ = other.prepare(self.reference, "test")
        self.assertNotEqual(first, second)

    def test_invalid_size_and_failed_status_pause_task(self):
        folder, task_id = self.service.prepare(self.reference, "test")
        self.complete(folder, task_id, size=(800, 800))
        with self.assertRaises(ValueError):
            self.service.receive(folder, task_id, self.root / "output")
        self.complete(folder, task_id, status="failed")
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            self.service.receive(folder, task_id, self.root / "output")

    def test_cancel_stops_waiting(self):
        def cancel():
            raise RuntimeError("cancelled")
        self.service.cancel_check = cancel
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.service.generate([str(self.reference)], "test", 1, self.root / "output")


if __name__ == "__main__":
    unittest.main()
