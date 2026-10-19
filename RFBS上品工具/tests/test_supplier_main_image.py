import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from supplier_1688 import SupplierWorkspace


class GeneratedMainTests(unittest.TestCase):
    def test_identifiers_are_unique_and_model_shared(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = SupplierWorkspace(folder)
            product = workspace.add_product({"title": "test", "url": "", "assets": []})
            first = product["variants"][0]
            second = workspace.add_variant(product, first)
            third = workspace.add_variant(product)
            self.assertEqual(len({v["offer_id"] for v in product["variants"]}), 3)
            self.assertEqual(len({v["model_name"] for v in product["variants"]}), 1)
            self.assertTrue(first["model_name"])
            old_offer = first["offer_id"]
            third["offer_id"] = ""
            workspace.ensure_identifiers(product, "CUSTOM-MODEL")
            self.assertEqual(first["offer_id"], old_offer)
            self.assertTrue(third["offer_id"])
            self.assertEqual({v["model_name"] for v in product["variants"]}, {"CUSTOM-MODEL"})

    def test_single_and_shared_preserve_attachments(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = SupplierWorkspace(folder)
            product = workspace.add_product({"title": "test", "url": "", "assets": []})
            first = product["variants"][0]
            first["images"] = ["old-main", "detail"]
            second = workspace.add_variant(product, first)
            path = Path(folder) / "main.png"
            Image.new("RGB", (900, 1200)).save(path)
            workspace.set_generated_main(product, [first["id"]], path)
            self.assertEqual(first["images"], [str(path), "detail"])
            self.assertEqual(second["images"], ["old-main", "detail"])
            workspace.set_generated_main(product, [first["id"], second["id"]], path)
            self.assertEqual(second["images"], [str(path), "detail"])
            with patch.object(workspace, "save", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    workspace.set_generated_main(product, [first["id"]], path)
            self.assertEqual(len(product["assets"]), 2)

    def test_invalid_image_does_not_change_variant(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = SupplierWorkspace(folder)
            product = workspace.add_product({"title": "test", "url": "", "assets": []})
            path = Path(folder) / "bad.png"
            Image.new("RGB", (100, 100)).save(path)
            with self.assertRaises(ValueError):
                workspace.set_generated_main(product, [product["variants"][0]["id"]], path)
            self.assertEqual(product["assets"], [])
            self.assertEqual(product["variants"][0]["images"], [])
