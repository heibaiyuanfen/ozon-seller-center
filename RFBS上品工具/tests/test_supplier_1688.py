import tempfile
import unittest
from pathlib import Path

from PIL import Image

from supplier_1688 import SupplierWorkspace, normalize_1688_url, original_1688_image


class SupplierTests(unittest.TestCase):
    def test_only_1688_offer_urls_are_accepted(self):
        self.assertEqual(normalize_1688_url("https://detail.1688.com/offer/123456.html?x=1"), "https://detail.1688.com/offer/123456.html")
        for url in ("https://1688.com.evil.test/offer/123.html", "https://example.com/offer/123.html", "https://1688.com/"):
            with self.assertRaises(ValueError):
                normalize_1688_url(url)

    def test_original_images_are_normalized_and_icons_excluded(self):
        self.assertEqual(original_1688_image("//cbu01.alicdn.com/img/ibank/test.jpg_60x60.jpg"), "https://cbu01.alicdn.com/img/ibank/test.jpg")
        self.assertEqual(original_1688_image("https://alicdn.com.evil.test/img/ibank/a.jpg"), "")
        self.assertEqual(original_1688_image("https://cbu01.alicdn.com/icons/avatar.png"), "")

    def test_variants_have_distinct_skus_and_independent_image_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = SupplierWorkspace(directory)
            product = workspace.add_product({"title": "test", "url": "url", "assets": []})
            first = product["variants"][0]
            first["images"] = ["original.jpg"]
            second = workspace.add_variant(product, first)
            second["images"].append("second.jpg")
            self.assertNotEqual(first["offer_id"], second["offer_id"])
            self.assertEqual(first["images"], ["original.jpg"])
            workspace.save()
            restored = SupplierWorkspace(directory)
            self.assertEqual(restored.data["products"][0]["variants"][1]["images"], second["images"])

    def test_delete_and_restore_assets_preserves_variant_images(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = SupplierWorkspace(directory)
            assets = [{"path": f"{index}.jpg"} for index in range(4)]
            product = workspace.add_product({"title": "test", "url": "url", "assets": assets})
            product["variants"][0]["images"] = ["1.jpg"]
            self.assertEqual(workspace.delete_assets(product, ["1", "3"]), 2)
            self.assertEqual([asset["path"] for asset in product["assets"]], ["0.jpg", "2.jpg"])
            self.assertEqual(product["variants"][0]["images"], ["1.jpg"])
            restored_workspace = SupplierWorkspace(directory)
            restored_product = restored_workspace.data["products"][0]
            self.assertEqual(restored_workspace.restore_assets(restored_product), 2)
            self.assertEqual({asset["path"] for asset in restored_product["assets"]}, {"0.jpg", "1.jpg", "2.jpg", "3.jpg"})
            self.assertEqual(restored_workspace.restore_assets(restored_product), 0)

    def test_edits_never_modify_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "original.png"
            Image.new("RGB", (900, 1200), "red").save(image)
            before = image.read_bytes()
            workspace = SupplierWorkspace(root / "workspace")
            rotated = workspace.edit_image(image, "rotate")
            with Image.open(rotated) as result:
                self.assertEqual(result.size, (1200, 900))
            cropped = workspace.edit_image(image, "crop", (0, 0, 300, 400))
            with Image.open(cropped) as result:
                self.assertEqual(result.size, (300, 400))
            self.assertEqual(image.read_bytes(), before)
            with self.assertRaises(ValueError):
                workspace.edit_image(image, "crop", (-1, 0, 900, 1200))
