import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import RfbsListingApp


class SupplierBatchTests(unittest.TestCase):
    def make_app(self):
        app = RfbsListingApp.__new__(RfbsListingApp)
        app._job_inputs_from_form = Mock(return_value={"model_name": "MODEL", "ozon_shop_id": "shop", "target_roi": "60"})
        app._expand_job_inputs_for_shops = lambda values, group: [{**values, "product_group_id": group, "ozon_shop_id": "shop", "ozon_shop_name": "Shop"}]
        def validate(values):
            if not values["local_image_paths"]:
                raise ValueError("missing images")
            values["price"] = str(float(values["purchase_cost"]) * 10)
        app._validate_job_inputs = validate
        app.auto_jobs, app.auto_job_order = {}, []
        app.auto_job_lock = threading.RLock()
        app.auto_job_queue = queue.Queue()
        app.listing_worker_limit = 1
        app.parallel_subprocess_enabled = True
        app.listing_workers_var = Mock()
        app._snapshot_local_job_images = lambda paths, offer, job: list(paths)
        app._save_auto_jobs = Mock()
        app._schedule_job_prefetch = Mock()
        app._refresh_job_tree = Mock()
        app._start_auto_job_worker = Mock()
        app.auto_status_var = Mock()
        app.notebook, app.workbench_tab = Mock(), Mock()
        return app

    def test_three_variants_are_queued_together_with_independent_prices(self):
        app = self.make_app()
        variants = [{"name": str(i), "offer_id": f"SKU-{i}", "purchase_cost": str(i + 1), "images": [f"{i}.jpg"], "weight": "100", "depth": "100", "width": "100", "height": "100"} for i in range(3)]
        with patch("app.messagebox.askyesno", return_value=True) as confirm:
            app._enqueue_supplier_variants({"id": "product", "url": "1688-url"}, variants)
        self.assertEqual(app.auto_job_queue.qsize(), 3)
        self.assertEqual(len(app.auto_jobs), 3)
        self.assertEqual({job["inputs"]["price"] for job in app.auto_jobs.values()}, {"10.0", "20.0", "30.0"})
        self.assertEqual(len({job["inputs"]["offer_id"] for job in app.auto_jobs.values()}), 3)
        self.assertEqual(app.listing_worker_limit, 3)
        app._start_auto_job_worker.assert_called_once()
        confirm.assert_called_once()

    def test_invalid_variant_prevents_entire_batch_from_starting(self):
        app = self.make_app()
        with self.assertRaisesRegex(ValueError, "missing images"):
            app._enqueue_supplier_variants({"id": "product", "url": "1688-url"}, [{"name": "bad", "images": []}])
        self.assertEqual(app.auto_jobs, {})
        app._start_auto_job_worker.assert_not_called()

    def test_failed_save_does_not_start_half_batch(self):
        app = self.make_app()
        app._save_auto_jobs.side_effect = OSError("disk error")
        with self.assertRaises(OSError):
            app._enqueue_prepared_inputs([{"listing_mode": "local", "local_image_paths": ["a.jpg"], "offer_id": "SKU"}], "test")
        self.assertEqual(app.auto_jobs, {})
        self.assertEqual(app.auto_job_order, [])
        self.assertTrue(app.auto_job_queue.empty())
