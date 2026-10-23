import tempfile
import unittest
from pathlib import Path
from product_ledger import listing_snapshot, upsert_product_ledger, read_listing_snapshot


class SnapshotTests(unittest.TestCase):
    def test_large_snapshot_round_trip_update_and_shop_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.xlsx"
            content = {"title_ru": "Название", "description_ru": "Описание😀" * 10000, "tags_ru": ["дом"]}
            item = {"offer_id": "A", "description_category_id": 123, "attributes": [{"id": 1, "values": [{"value": "材质"}]}], "images": ["secret-image-url"], "primary_image": "image"}
            raw = listing_snapshot(item, content)
            for shop in ("one", "two"):
                upsert_product_ledger(path, {"platform": "Ozon", "shop_config_id": shop, "offer_id": "A", "listing_snapshot_json": raw})
            saved = read_listing_snapshot(path, "Ozon", "one", "A")
            self.assertEqual(saved["content"], content)
            self.assertEqual(saved["item"]["attributes"], item["attributes"])
            self.assertNotIn("images", saved["item"])
            self.assertNotIn("primary_image", saved["item"])
            upsert_product_ledger(path, {"platform": "Ozon", "shop_config_id": "one", "offer_id": "A", "listing_snapshot_json": listing_snapshot({"name": "new"}, {})})
            self.assertEqual(read_listing_snapshot(path, "Ozon", "one", "A")["item"], {"name": "new"})
            self.assertEqual(read_listing_snapshot(path, "Ozon", "two", "A")["content"], content)
