import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import RfbsListingApp
from shop_variants import DuplicateMainImageError, ShopVariantRegistry, shop_offer_id, variant_directory


class ShopVariantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first = self.root / "first.png"
        self.second = self.root / "second.png"
        image = Image.new("RGB", (300, 400), "white")
        ImageDraw.Draw(image).rectangle((50, 80, 220, 330), fill="navy")
        image.save(self.first)
        image = Image.new("RGB", (300, 400), "orange")
        ImageDraw.Draw(image).ellipse((100, 30, 270, 260), fill="green")
        image.save(self.second)
        self.registry_path = self.root / "variants.db"

    def test_offer_is_stable_distinct_and_bounded_even_for_truncated_bases(self):
        first = shop_offer_id("SKU", "shop-a")
        self.assertEqual(first, shop_offer_id("SKU", "shop-a"))
        self.assertNotEqual(first, shop_offer_id("SKU", "shop-b"))
        self.assertEqual(len(shop_offer_id("X" * 200, "shop-a")), 100)
        self.assertNotEqual(shop_offer_id("X" * 200, "shop-a"), shop_offer_id("X" * 201, "shop-a"))

    def test_reencoded_and_resized_cover_is_rejected_across_registry_instances(self):
        first = ShopVariantRegistry(self.registry_path)
        first.register_main("group", "a", "context-a", self.first)
        duplicate = self.root / "reencoded.jpg"
        with Image.open(self.first) as image:
            image.resize((600, 800)).save(duplicate, quality=90)
        second = ShopVariantRegistry(self.registry_path)
        with self.assertRaises(DuplicateMainImageError):
            second.register_main("group", "b", "context-b", duplicate)
        second.register_main("group", "b", "context-b", self.second)
        second.register_main("other-group", "c", "context-c", duplicate)

    def test_cache_detects_modified_or_missing_file_and_changed_context(self):
        registry = ShopVariantRegistry(self.registry_path)
        registry.register_main("group", "a", "context", self.first)
        self.assertEqual(registry.cached_main("group", "a", "context"), str(self.first.resolve()))
        self.assertIsNone(registry.cached_main("group", "a", "changed"))
        shutil.copyfile(self.second, self.first)
        self.assertIsNone(registry.cached_main("group", "a", "context"))
        self.first.unlink()
        self.assertIsNone(registry.cached_main("group", "a", "context"))

    def test_concurrent_duplicate_reservations_only_accept_one_shop(self):
        ShopVariantRegistry(self.registry_path)
        def reserve(shop):
            try:
                ShopVariantRegistry(self.registry_path).register_main("group", shop, shop, self.first)
                return True
            except DuplicateMainImageError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(reserve, ["a", "b"])), 1)

    def test_unicode_and_sanitized_folder_names_do_not_collide(self):
        self.assertNotEqual(variant_directory(self.root, "group", "店铺一"), variant_directory(self.root, "group", "店铺二"))
        self.assertNotEqual(variant_directory(self.root, "a/b", "shop"), variant_directory(self.root, "a_b", "shop"))

    def test_repair_preserves_batch_and_main_requirement_and_reassigns_offer_on_shop_change(self):
        original = {
            "product_group_id": "group", "base_offer_id": "SKU", "unique_main_image": "1",
            "ozon_shop_id": "a", "offer_id": shop_offer_id("SKU", "a"),
        }
        unchanged_shop = RfbsListingApp._merge_repaired_job_inputs(original, {"price": "120"})
        self.assertEqual(unchanged_shop["unique_main_image"], "1")
        self.assertEqual(unchanged_shop["offer_id"], original["offer_id"])
        repaired = RfbsListingApp._merge_repaired_job_inputs(original, {"ozon_shop_id": "b"})
        self.assertEqual(repaired["offer_id"], shop_offer_id("SKU", "b"))
        self.assertEqual(repaired["product_group_id"], "group")
        self.assertEqual(original["ozon_shop_id"], "a")
        self.assertEqual(
            RfbsListingApp._shop_main_image_direction({"ozon_shop_id": "a", "shop_variant_index": 0}),
            RfbsListingApp._shop_main_image_direction({"ozon_shop_id": "a", "shop_variant_index": 3}),
        )

    def _app(self, shop):
        app = RfbsListingApp.__new__(RfbsListingApp)
        app.active_job_id = shop
        app.auto_jobs = {shop: {"inputs": {
            "product_group_id": "group", "ozon_shop_id": shop,
            "base_offer_id": "SKU", "offer_id": shop_offer_id("SKU", shop),
        }}}
        app._log = lambda _message: None
        app._save_auto_jobs = lambda: None
        return app

    def test_generation_retries_duplicate_then_reuses_main_and_shared_attachments(self):
        owner = self
        class Generator:
            api_url = "https://example.invalid/images/edits"
            model = "test"
            def __init__(self, sources):
                self.sources = iter(sources)
                self.calls = 0
            def generate(self, references, prompt, count, directory, *, exact_3_4=False):
                owner.assertTrue(exact_3_4)
                owner.assertEqual(references, [str(owner.first)])
                self.calls += 1
                output = Path(directory) / "cover.png"
                output.parent.mkdir(parents=True)
                shutil.copyfile(next(self.sources), output)
                return [str(output)]
        references = [str(self.first), str(self.second)]
        with patch("app.SHOP_VARIANTS_PATH", self.registry_path), patch("app.OUTPUT_DIR", self.root / "output"):
            a = self._app("a")
            first = a._generate_storefront_images(Generator([self.first]), references, 0, "prompt-a", "", "covers")
            b = self._app("b")
            generator = Generator([self.first, self.second])
            second = b._generate_storefront_images(generator, references, 0, "prompt-b", "", "covers")
            self.assertEqual(generator.calls, 2)
            self.assertNotEqual(first[0], second[0])
            self.assertEqual(first[1:], second[1:])
            restored = self._app("b")
            restored._generate_storefront_images(generator, references, 0, "prompt-b", "", "covers")
            self.assertEqual(generator.calls, 2)
            self.assertEqual(restored.auto_jobs["b"]["variant"]["main_image"], second[0])
            rejected = self._app("c")
            with self.assertRaisesRegex(RuntimeError, "任务已暂停"):
                rejected._generate_storefront_images(Generator([self.first] * 3), references, 0, "prompt-c", "", "covers")

    def test_local_multi_shop_pipeline_selects_generation_and_stops_before_upload_on_failure(self):
        app = self._app("a")
        job = app.auto_jobs["a"]
        job.update({"id": "a", "stage": 2})
        job["inputs"].update({"listing_mode": "local", "unique_main_image": "1"})
        events = []
        app._apply_job_context = lambda _job: None
        app._set_auto_progress = lambda *_args: None
        app._retry_step = lambda _label, operation, **_kwargs: operation()
        def fail_generation():
            events.append("generate")
            raise RuntimeError("duplicate cover")
        app._generate_amazon_local_main = fail_generation
        app._prepare_local_images_for_upload = lambda: events.append("original")
        app._upload_images = lambda: events.append("upload")
        with self.assertRaisesRegex(RuntimeError, "duplicate cover"):
            app._run_auto_job(job)
        self.assertEqual(events, ["generate"])
        self.assertEqual(job["stage"], 2)


if __name__ == "__main__":
    unittest.main()
