import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared_source import collect_shared_source


def process_collect(root, results):
    root = Path(root)
    def collect():
        (root / ("collected-" + str(os.getpid()))).write_text("once")
        time.sleep(0.15)
        return {"reference": {"title": "product A"}, "source_images": [str(root / "original.jpg")]}
    state = collect_shared_source(root / "shared", "batch|product-A", collect)
    results.put(state["source_images"])


class SharedSourceTests(unittest.TestCase):
    def test_three_processes_collect_and_download_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "original.jpg").write_bytes(b"image")
            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            workers = [context.Process(target=process_collect, args=(directory, results)) for _ in range(3)]
            for worker in workers:
                worker.start()
            try:
                paths = [results.get(timeout=15) for _ in workers]
            finally:
                for worker in workers:
                    worker.join(15)
                    if worker.is_alive():
                        worker.terminate()
                        worker.join()
                results.close()
                results.join_thread()
            self.assertTrue(all(worker.exitcode == 0 for worker in workers))
            self.assertEqual(len(list(root.glob("collected-*"))), 1)
            self.assertTrue(all(value == [str(root / "original.jpg")] for value in paths))

    def test_prefetch_and_listing_threads_share_one_collection_and_persistent_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "original.jpg"
            image.write_bytes(b"original")
            calls = []
            def collect():
                calls.append(1)
                time.sleep(0.05)
                return {"reference": {"title": "A"}, "source_images": [str(image)]}
            with ThreadPoolExecutor(max_workers=3) as workers:
                results = list(workers.map(lambda _: collect_shared_source(directory, "A", collect), range(3)))
            collect_shared_source(directory, "A", collect)
            self.assertEqual(len(calls), 1)
            self.assertEqual(results[0], results[1])
            image.unlink()
            def recollect():
                calls.append(1)
                image.write_bytes(b"new original")
                return {"reference": {"title": "A"}, "source_images": [str(image)]}
            collect_shared_source(directory, "A", recollect)
            self.assertEqual(len(calls), 2)

    def test_failed_collector_releases_lock_for_later_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail():
                raise RuntimeError("network timeout")
            with self.assertRaisesRegex(RuntimeError, "network timeout"):
                collect_shared_source(directory, "A", fail)
            image = Path(directory) / "image.jpg"
            image.write_bytes(b"image")
            state = collect_shared_source(directory, "A", lambda: {"reference": {"title": "A"}, "source_images": [str(image)]})
            self.assertEqual(state["reference"]["title"], "A")

    def test_different_products_have_independent_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            for key in ["A", "B"]:
                image = Path(directory) / (key + ".jpg")
                image.write_bytes(key.encode())
                state = collect_shared_source(directory, key, lambda: {"reference": {"title": key}, "source_images": [str(image)]})
                self.assertEqual(state["reference"]["title"], key)


if __name__ == "__main__":
    unittest.main()
