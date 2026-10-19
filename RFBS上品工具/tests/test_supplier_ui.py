import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from supplier_ui import SupplierPanel


class SupplierUITests(unittest.TestCase):
    def test_clear_records_preserves_data_and_batch_uses_visible_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            try:
                batches = []
                panel = SupplierPanel(root, directory, lambda *args: None, lambda product, variants: batches.append([variant["id"] for variant in variants]))
                product = panel.workspace.add_product({"title": "Product", "url": "1688-url", "assets": []})
                panel.workspace.add_variant(product)
                panel.workspace.add_variant(product)
                panel.refresh_products()
                panel.batch_apply()
                self.assertEqual(len(batches[-1]), 3)
                with patch("supplier_ui.messagebox.askyesno", return_value=True):
                    panel.delete_variant()
                self.assertEqual(len(product["variants"]), 3)
                self.assertEqual(panel.variants.size(), 2)
                panel.batch_apply()
                self.assertEqual(len(batches[-1]), 2)
                panel.restore_variant_records()
                self.assertEqual(panel.variants.size(), 3)
                with patch("supplier_ui.messagebox.askyesno", return_value=True):
                    panel.clear_product_record()
                self.assertEqual(len(panel.workspace.data["products"]), 1)
                self.assertIsNone(panel.product)
                panel.restore_product_records()
                self.assertEqual(panel.product["id"], product["id"])
            finally:
                root.destroy()

    def test_select_copy_edit_and_apply_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            panel = None
            try:
                applied = []
                panel = SupplierPanel(root, Path(directory) / "workspace", lambda product, variant: applied.append((product, variant)))
                assets = []
                for index in range(2):
                    path = Path(directory) / f"{index}.jpg"
                    Image.new("RGB", (900, 1200), (index * 150, 20, 30)).save(path)
                    assets.append({"path": str(path), "width": 900, "height": 1200, "group": "规格", "label": str(index)})
                panel.workspace.add_product({"url": "https://detail.1688.com/offer/123.html", "title": "Test", "assets": assets, "failures": 0})
                panel.refresh_products()
                panel.assets.selection_set("0", "1")
                panel.assign_images()
                first = panel.variant
                self.assertEqual(len(first["images"]), 2)
                panel.new_variant(True)
                second = panel.variant
                self.assertNotEqual(first["offer_id"], second["offer_id"])
                panel.chosen.selection_set(0)
                panel.edit_image("rotate")
                self.assertNotEqual(second["images"][0], first["images"][0])
                panel.chosen.selection_clear(0, tk.END)
                panel.chosen.selection_set(1)
                panel.move_image(None)
                self.assertEqual(second["images"][0], assets[1]["path"])
                panel.apply()
                self.assertEqual(applied[0][1]["offer_id"], second["offer_id"])
                panel.asset_filter.set("详情")
                panel.refresh_assets()
                self.assertEqual(panel.assets.get_children(), ())
            finally:
                root.destroy()
