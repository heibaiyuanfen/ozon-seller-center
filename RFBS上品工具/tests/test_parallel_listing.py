import copy
import queue
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import RfbsListingApp
from core import atomic_write_json, load_json
from parallel_worker import run_parallel_job


class ParallelListingTests(unittest.TestCase):
    def make_app(self, limit=2):
        app = RfbsListingApp.__new__(RfbsListingApp)
        app.auto_job_lock = threading.RLock()
        app.auto_job_queue = queue.Queue()
        app.auto_jobs = {}
        app.auto_job_order = []
        app.auto_job_workers_active = 0
        app.auto_job_worker_running = False
        app.active_job_ids = set()
        app.active_job_processes = {}
        app.prefetch_futures = {}
        app.parallel_subprocess_enabled = True
        app.listing_worker_limit = limit
        app._closing = False
        app.root = SimpleNamespace(after=lambda _delay, callback: callback())
        app.auto_status_var = SimpleNamespace(set=lambda _value: None)
        app._save_auto_jobs = lambda: None
        app._refresh_job_tree = lambda: None
        app._log = lambda _message: None
        return app

    def enqueue(self, app, job_id, shop, group="same-product"):
        job = {
            "id": job_id, "status": "queued", "stage": 0, "state": {},
            "inputs": {"ozon_shop_id": shop, "product_group_id": group, "offer_id": job_id},
        }
        app.auto_jobs[job_id] = job
        app.auto_job_order.append(job_id)
        app.auto_job_queue.put(job_id)
        return job

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate(), "并发任务未在测试期限内达到预期状态")

    def test_same_product_two_shops_overlap_and_obey_concurrency_limit(self):
        app = self.make_app(2)
        for job_id, shop in (("a", "shop-a"), ("b", "shop-b"), ("c", "shop-c")):
            self.enqueue(app, job_id, shop)
        release = threading.Event()
        self.addCleanup(release.set)
        active, peak, pairs = set(), [0], []
        def run(job):
            with app.auto_job_lock:
                active.add(job["id"])
                peak[0] = max(peak[0], len(active))
                if len(active) == 2:
                    pairs.append(set(active))
            if not release.wait(3):
                raise RuntimeError("测试未释放任务")
            with app.auto_job_lock:
                active.remove(job["id"])
                job.update(status="completed", stage=7)
        app._run_job_subprocess = run
        app._start_auto_job_worker()
        try:
            self.wait_until(lambda: len(active) == 2)
            self.assertEqual(active, {"a", "b"})
            self.assertEqual(app.auto_jobs["c"]["status"], "queued")
            self.assertEqual(app.active_job_ids, {"a", "b"})
        finally:
            release.set()
        self.wait_until(lambda: app.auto_job_workers_active == 0)
        self.assertEqual(peak[0], 2)
        self.assertIn({"a", "b"}, pairs)
        self.assertTrue(all(job["status"] == "completed" for job in app.auto_jobs.values()))
        self.assertEqual(app.auto_job_queue.unfinished_tasks, 0)

    def test_lowering_limit_waits_for_active_tasks_and_blocks_new_start(self):
        app = self.make_app(2)
        for job_id in ("a", "b", "c"):
            self.enqueue(app, job_id, job_id)
        started = {job_id: threading.Event() for job_id in app.auto_jobs}
        release = {job_id: threading.Event() for job_id in app.auto_jobs}
        self.addCleanup(lambda: [event.set() for event in release.values()])
        def run(job):
            started[job["id"]].set()
            if not release[job["id"]].wait(3):
                raise RuntimeError("测试未释放任务")
            with app.auto_job_lock:
                job.update(status="completed", stage=7)
        app._run_job_subprocess = run
        app._start_auto_job_worker()
        self.wait_until(lambda: started["a"].is_set() and started["b"].is_set())
        with app.auto_job_lock:
            app.listing_worker_limit = 1
        release["a"].set()
        self.wait_until(lambda: app.auto_job_workers_active == 1)
        self.assertFalse(started["c"].is_set())
        self.assertEqual(app.auto_jobs["b"]["status"], "running")
        release["b"].set()
        self.wait_until(started["c"].is_set)
        release["c"].set()
        self.wait_until(lambda: app.auto_job_workers_active == 0)
        self.assertEqual(app.auto_job_queue.unfinished_tasks, 0)

    def test_increasing_limit_starts_second_task_without_waiting_for_first(self):
        app = self.make_app(1)
        for job_id in ("a", "b"):
            self.enqueue(app, job_id, job_id)
        started = {job_id: threading.Event() for job_id in app.auto_jobs}
        release = threading.Event()
        self.addCleanup(release.set)
        def run(job):
            started[job["id"]].set()
            if not release.wait(3):
                raise RuntimeError("测试未释放任务")
            with app.auto_job_lock:
                job.update(status="completed", stage=7)
        app._run_job_subprocess = run
        app._start_auto_job_worker()
        self.wait_until(started["a"].is_set)
        self.assertFalse(started["b"].is_set())
        app.listing_workers_var = SimpleNamespace(get=lambda: 2, set=lambda _value: None)
        app._on_listing_workers_changed()
        self.wait_until(started["b"].is_set)
        release.set()
        self.wait_until(lambda: app.auto_job_workers_active == 0)

    def test_failed_task_does_not_interrupt_other_parallel_task(self):
        app = self.make_app(2)
        bad = self.enqueue(app, "bad", "a")
        good = self.enqueue(app, "good", "b")
        overlap = threading.Barrier(2)
        def run(job):
            overlap.wait(timeout=3)
            if job["id"] == "bad":
                raise RuntimeError("bad attributes")
            with app.auto_job_lock:
                job.update(status="completed", stage=7)
        app._run_job_subprocess = run
        app._start_auto_job_worker()
        self.wait_until(lambda: app.auto_job_workers_active == 0)
        self.assertEqual(bad["status"], "failed")
        self.assertEqual(good["status"], "completed")

    def test_snapshot_does_not_alias_other_shop_or_copy_its_uploaded_images(self):
        app = self.make_app()
        a = self.enqueue(app, "a", "shop-a")
        a.update(stage=4, state={
            "source_images": ["original.jpg"], "local_images": ["shop-a-main.jpg"],
            "uploaded_urls": ["shop-a-oss"], "fields": {"title": "shared title"},
        })
        b = self.enqueue(app, "b", "shop-b")
        snapshot = app._parallel_job_snapshot(b)
        self.assertEqual(snapshot["stage"], 2)
        self.assertEqual(snapshot["state"]["local_images"], ["original.jpg"])
        self.assertEqual(snapshot["state"]["uploaded_urls"], [])
        snapshot["state"]["fields"]["title"] = "changed"
        self.assertEqual(a["state"]["fields"]["title"], "shared title")
        self.assertEqual(b["state"]["fields"]["title"], "shared title")
        local = self.enqueue(app, "local", "shop-c")
        local["inputs"]["listing_mode"] = "local"
        self.assertEqual(app._parallel_job_snapshot(local)["stage"], 0)

    def test_completed_prefetch_is_copied_into_each_worker_snapshot(self):
        app = self.make_app()
        future = Future()
        future.set_result({"source_images": ["original.jpg"]})
        app.prefetch_futures["same-product"] = future
        a = self.enqueue(app, "a", "a")
        snapshot = app._parallel_job_snapshot(a)
        snapshot["prefetch_state"]["source_images"].append("changed")
        self.assertEqual(future.result()["source_images"], ["original.jpg"])

    def test_stopping_status_survives_progress_and_final_cancellation_keeps_task_id(self):
        app = self.make_app()
        job = self.enqueue(app, "a", "a")
        job.update(status="stopping", cancel_requested=True)
        app._apply_parallel_job_progress(job, {"id": "a", "status": "running", "cancel_requested": False, "task_id": 123})
        self.assertEqual(job["status"], "stopping")
        self.assertTrue(job["cancel_requested"])
        app._apply_parallel_job_progress(job, {"id": "a", "status": "cancelled", "cancel_requested": False}, final=True)
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["task_id"], 123)
        before = copy.deepcopy(job)
        with self.assertRaises(ValueError):
            app._apply_parallel_job_progress(job, {"id": "different"})
        self.assertEqual(job, before)

    def test_stop_writes_only_selected_marker_and_never_kills_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = self.make_app()
            selected = self.enqueue(app, "a", "a")
            other = self.enqueue(app, "b", "b")
            selected["status"] = "running"
            app._selected_job = lambda: selected
            process = Mock()
            app.active_job_processes["a"] = process
            with patch("app.APP_DIR", Path(temporary)), patch("app.messagebox.askyesno", return_value=True):
                app._stop_selected_job()
            self.assertTrue((Path(temporary) / "parallel-jobs/a.cancel.json").is_file())
            self.assertFalse((Path(temporary) / "parallel-jobs/b.cancel.json").exists())
            self.assertEqual(other["status"], "queued")
            process.terminate.assert_not_called()

    def test_child_cooperative_cancel_returns_saved_import_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cancel = atomic_write_json(root / "cancel.json", {"cancel": True})
            request = atomic_write_json(root / "request.json", {
                "job": {"id": "a", "stage": 6, "inputs": {}}, "cancel_path": str(cancel),
            })
            app = SimpleNamespace(
                prefetch_executor=Mock(), _check_auto_job_cancelled=RfbsListingApp._check_auto_job_cancelled,
            )
            def run(job):
                job["task_id"] = 123
                app._check_auto_job_cancelled(job)
            app._run_auto_job = run
            with patch("app.RfbsListingApp", return_value=app), patch("parallel_worker.tk.Tk", return_value=Mock()), patch.dict("os.environ", {}, clear=False):
                result = run_parallel_job(str(request), str(root / "result.json"))
            payload = load_json(root / "result.json", {})
            self.assertEqual(result, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["job"]["status"], "cancelled")
            self.assertEqual(payload["job"]["task_id"], 123)

    def test_nonisolated_execution_is_always_serial_and_counts_are_bounded(self):
        app = self.make_app(5)
        app.parallel_subprocess_enabled = False
        self.assertEqual(app._listing_concurrency(), 1)
        self.assertEqual(app._bounded_worker_count("invalid"), 3)
        self.assertEqual(app._bounded_worker_count(100), 5)
        self.assertEqual(app._bounded_worker_count(0), 1)

    def test_listing_limit_is_restored_independently_of_prefetch_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.json"
            app = self.make_app(4)
            with patch("app.AUTO_JOBS_PATH", path):
                RfbsListingApp._save_auto_jobs(app)
                app.listing_worker_limit = 1
                app.vars = {}
                app.ozon_shops = {}
                app.listing_workers_var = Mock()
                app._load_auto_jobs()
            self.assertEqual(app._listing_concurrency(), 4)
            app.listing_workers_var.set.assert_called_once_with(4)

    def test_browser_fallback_uses_separate_profile_for_each_active_job(self):
        profiles = []
        app = self.make_app()
        app.vars = {"source_url": SimpleNamespace(get=lambda: "123456789")}
        with patch("app.scrape_reference", side_effect=ValueError("test before network")) as scrape:
            for job_id in ("a", "b"):
                app.active_job_id = job_id
                with self.assertRaises(ValueError):
                    app._parse_url()
                profiles.append(scrape.call_args.kwargs["profile_dir"])
        self.assertNotEqual(profiles[0], profiles[1])


if __name__ == "__main__":
    unittest.main()
