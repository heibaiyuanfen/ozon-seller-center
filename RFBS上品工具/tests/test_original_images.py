import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app import RfbsListingApp


class OriginalImagesTests(unittest.TestCase):
    def test_all_image_entry_points_keep_original_bytes_and_skip_services(self):
        for method in ("_generate_images", "_prepare_local_images_for_upload", "_generate_amazon_local_main"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                paths = [Path(directory) / name for name in ("first.jpg", "second.jpg")]
                for index, path in enumerate(paths):
                    path.write_bytes(f"original-{index}".encode())
                before = [path.read_bytes() for path in paths]
                app = RfbsListingApp.__new__(RfbsListingApp)
                mode = Mock()
                mode.get.return_value = "original"
                app.vars = {"image_mode": mode}
                app.source_images = [str(path) for path in paths]
                app.local_images = []
                app.primary_reference_index = 1
                app.uploaded_urls = ["old-url"]
                app.active_job_id = "job"
                app.auto_jobs = {"job": {"variant": {}, "image_fallback": {}}}
                app._replace_images = Mock()
                app._log = Mock()
                app._image_generation_service = Mock(side_effect=AssertionError("must not generate"))
                getattr(app, method)()
                app._replace_images.assert_called_once_with([str(paths[1]), str(paths[0])])
                self.assertEqual(before, [path.read_bytes() for path in paths])
                self.assertEqual(app.uploaded_urls, [])
                self.assertNotIn("variant", app.auto_jobs["job"])
                app._image_generation_service.assert_not_called()

    def test_missing_original_pauses_before_upload(self):
        app = RfbsListingApp.__new__(RfbsListingApp)
        app.source_images = ["missing-original.jpg"]
        app.local_images = []
        app.primary_reference_index = 0
        app._replace_images = Mock()
        with self.assertRaises(FileNotFoundError):
            app._use_original_images()
        app._replace_images.assert_not_called()
