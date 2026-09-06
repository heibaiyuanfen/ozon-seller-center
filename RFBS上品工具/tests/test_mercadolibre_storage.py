import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from mercadolibre.models import ListingPlan, Picture, Procurement, ProductDraft
from mercadolibre.storage import MercadoLibreStore


class MercadoLibreStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.store = MercadoLibreStore(self.directory / "mercadolibre-data")

    def make_image(self, name="source.png", image_format="PNG"):
        path = self.directory / name
        Image.new("RGB", (30, 40), "red").save(path, format=image_format)
        return path

    def test_roundtrip_preserves_nested_fields_in_independent_directory(self):
        unrelated = self.directory / "ozon-settings.json"
        unrelated.write_text('{"shop": "unchanged"}', encoding="utf-8")
        draft = ProductDraft(
            title="台灯", seller_sku="ML-LAMP-01", price="39.99",
            procurement=Procurement(supplier_url="https://example.com/product", cost_cny="45"),
            pictures=[Picture(source="https://example.com/lamp.jpg")],
        )
        path = self.store.save_draft(draft)
        reloaded = MercadoLibreStore(self.store.root)
        self.assertEqual(path, self.store.root / "products" / draft.id / "draft.json")
        self.assertEqual(reloaded.load_draft(draft.id), draft)
        self.assertEqual(reloaded.list_drafts(), [draft])
        self.assertEqual(unrelated.read_text(encoding="utf-8"), '{"shop": "unchanged"}')

    def test_broken_draft_is_skipped_reported_and_never_overwritten(self):
        broken = ProductDraft()
        broken_path = self.store.save_draft(broken)
        malformed = b'{"schema_version": 1, "title": '
        broken_path.write_bytes(malformed)
        good = ProductDraft(title="working draft")
        self.store.save_draft(good)
        self.assertEqual(self.store.list_drafts(), [good])
        self.assertTrue(self.store.warnings)
        with self.assertRaisesRegex(ValueError, "已有草稿无法读取"):
            self.store.save_draft(broken)
        self.assertEqual(broken_path.read_bytes(), malformed)
        self.assertFalse(list(broken_path.parent.glob(".tmp-*")))

    def test_id_mismatch_and_unknown_schema_do_not_replace_existing_draft(self):
        draft = ProductDraft()
        path = self.store.save_draft(draft)
        for changes in ({"id": "f" * 32}, {"schema_version": 999}, {"title": {"bad": "type"}}):
            invalid = {**draft.to_dict(), **changes}
            path.write_text(json.dumps(invalid), encoding="utf-8")
            old_bytes = path.read_bytes()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.save_draft(draft)
            self.assertEqual(path.read_bytes(), old_bytes)

    def test_all_product_operations_reject_path_traversal(self):
        for draft_id in ("../outside", "a" * 31, "A" * 32, "a" * 32 + "/file", "", None):
            plan = ListingPlan(draft_id, "global_selling", "prepared", {}, [], [])
            for operation in (
                lambda: self.store.load_draft(draft_id),
                lambda: self.store.save_draft(ProductDraft(id=draft_id)),
                lambda: self.store.import_images(draft_id, []),
                lambda: self.store.save_plan(plan),
            ):
                with self.subTest(draft_id=draft_id), self.assertRaises(ValueError):
                    operation()
        self.assertFalse(self.store.root.exists())

    def test_images_are_copied_numbered_per_product_without_claiming_upload(self):
        png = self.make_image()
        jpeg = self.make_image("photo.jpeg", "JPEG")
        original = png.read_bytes()
        first, second = ProductDraft(), ProductDraft()
        pictures = self.store.import_images(first.id, [png, jpeg])
        extra = self.store.import_images(first.id, [png])
        separate = self.store.import_images(second.id, [png])
        self.assertEqual([Path(p.local_path).name[:3] for p in pictures + extra], ["01_", "02_", "03_"])
        self.assertTrue(Path(separate[0].local_path).name.startswith("01_"))
        self.assertNotEqual(Path(pictures[0].local_path).parent, Path(separate[0].local_path).parent)
        self.assertEqual(png.read_bytes(), original)
        self.assertEqual(Path(pictures[0].local_path).read_bytes(), original)
        for picture in pictures + extra + separate:
            self.assertTrue(Path(picture.local_path).is_absolute())
            self.assertEqual((picture.picture_id, picture.source), ("", ""))

    def test_entire_image_batch_is_validated_before_any_copy(self):
        valid = self.make_image()
        invalid = self.directory / "pretend.jpg"
        invalid.write_text("This is not an image", encoding="utf-8")
        draft = ProductDraft()
        with self.assertRaisesRegex(ValueError, "无效的 JPEG/PNG"):
            self.store.import_images(draft.id, [valid, invalid])
        self.assertFalse(self.store.root.exists())
        gif = self.make_image("unsupported.gif", "GIF")
        with self.assertRaises(ValueError):
            self.store.import_images(draft.id, [gif])
        with patch("mercadolibre.storage.MAX_IMAGE_BYTES", 5), self.assertRaises(ValueError):
            self.store.import_images(draft.id, [valid])

    def test_plan_snapshots_do_not_overwrite_each_other_or_change_draft(self):
        draft = ProductDraft()
        draft_path = self.store.save_draft(draft)
        original = draft_path.read_bytes()
        plan = ListingPlan(draft.id, "user_products", "prepared", {"price": 25}, [], [])
        first, second = self.store.save_plan(plan), self.store.save_plan(plan)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, draft_path.parent / "plans")
        self.assertEqual(json.loads(first.read_text(encoding="utf-8"))["plan"], plan.to_dict())
        self.assertEqual(draft_path.read_bytes(), original)

    def test_settings_allowlist_rejects_secrets_without_changing_file(self):
        settings = {"app_id": "1234", "redirect_uri": "https://example.com/callback", "account_label": "ML"}
        path = self.store.save_settings(settings)
        self.assertEqual(self.store.load_settings(), settings)
        old_bytes = path.read_bytes()
        for key in ("access_token", "refresh_token", "client_secret", "password", "nested"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.save_settings({**settings, key: "do-not-save"})
            self.assertEqual(path.read_bytes(), old_bytes)
        path.write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.store.load_settings(), {})
        self.assertTrue(self.store.warnings)

    def test_failed_atomic_replace_retains_previous_draft_and_removes_temp(self):
        draft = ProductDraft(title="original")
        path = self.store.save_draft(draft)
        original = path.read_bytes()
        draft.title = "new value"
        with patch("mercadolibre.storage.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.save_draft(draft)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(path.parent.glob(".tmp-*")))

    def test_product_directory_symlink_cannot_redirect_writes(self):
        outside = self.directory / "outside"
        outside.mkdir()
        products = self.store.root / "products"
        products.mkdir(parents=True)
        draft = ProductDraft()
        try:
            (products / draft.id).symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("The current Windows account cannot create symlinks")
        with self.assertRaises(ValueError):
            self.store.save_draft(draft)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
