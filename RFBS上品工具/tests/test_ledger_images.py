import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import RfbsListingApp
from core import ReferenceProduct, validate_required
from catalog_upload import CatalogOutbox, product_data
from erp_ledger import read_ledger
from product_ledger import image_link_fields, ledger_image_urls, upsert_product_ledger, backfill_ledger_image_links


class ModelCategoryTests(unittest.TestCase):
    def application(self):
        app = RfbsListingApp.__new__(RfbsListingApp)
        app.vars = {key: Mock(get=Mock(return_value=value)) for key, value in
                    {'category_id': '1', 'type_id': '2', 'model_name': 'MODEL'}.items()}
        app.attribute_definitions = [{'id': 111, 'name': 'Material', 'is_required': True}]
        app._localized_attributes = Mock(return_value=app.attribute_definitions)
        app.reference = ReferenceProduct('', 'Product', '', [], {}, [])
        app._api = Mock()
        app._log = Mock()
        app._generated_content = Mock(return_value={})
        app._write_attribute_documents = Mock()
        app._refresh_attribute_tree = Mock()
        app.root = Mock()
        app.root.after.side_effect = lambda delay, callback: callback()
        return app

    def test_missing_model_does_not_invent_attribute_or_bypass_required_fields(self):
        app = self.application()
        attributes = []
        self.assertFalse(app._enforce_manual_model_name(attributes, []))
        self.assertEqual(attributes, [])
        self.assertEqual(validate_required(attributes, app.attribute_definitions, []), ['Material'])
        app._load_attributes()
        app._write_attribute_documents.assert_called_once()

    def test_russian_model_label_with_explanation_is_recognized(self):
        app = self.application()
        app.attribute_definitions = [{'id': 9048, 'name': 'Название модели (для объединения в одну карточку)'}]
        attributes = []
        self.assertTrue(app._enforce_manual_model_name(attributes, []))
        self.assertEqual(attributes[0]['values'][0]['value'], 'MODEL')

    def test_ai_mapping_can_load_category_without_model_attribute(self):
        app = self.application()
        category = {'description_category_id': 1, 'type_id': 2, 'display': 'Category'}
        app.categories = [category]
        app.category_combo = Mock()
        app.source_images = app.local_images = []
        app._config = Mock(return_value={'text': {'api_url': 'https://ai.example.com', 'api_key': 'test', 'model': 'test'}})
        service = Mock(api_url='https://ai.example.com')
        service.choose_category.return_value = (category, [])
        service.map_attributes.return_value = []
        with patch('app.CopywritingService', return_value=service):
            app._ai_fill_category_attributes()
        app._write_attribute_documents.assert_called_once()


class LedgerImageTests(unittest.TestCase):
    primary = 'https://images.example.com/main.jpg'
    extra = 'https://images.example.com/extra.jpg'

    def record(self, **values):
        return {'platform': 'Ozon', 'shop_config_id': 'shop-1', 'offer_id': 'SKU',
                'import_task_id': 123, 'title': 'Product', **values}

    def submission(self, directory, **overrides):
        value = {'submitted': True, 'task_id': 123,
                 'item': {'offer_id': 'SKU', 'primary_image': self.primary, 'images': [self.primary, self.extra]},
                 'import_result': {'result': {'items': [{'offer_id': 'SKU', 'status': 'imported', 'errors': []}]}},
                 **overrides}
        (directory / 'submitted-SKU.json').write_text(json.dumps(value), encoding='utf-8')

    def test_workbook_round_trip_and_catalog_upload_preserve_primary_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.xlsx'
            fields = image_link_fields(self.primary, [self.extra, self.primary])
            upsert_product_ledger(path, self.record(**fields))
            _, row = next(read_ledger(path))
            self.assertEqual(row['primary_image_url'], self.primary)
            self.assertEqual(row['additional_image_urls'], self.extra)
            self.assertEqual(ledger_image_urls(row), [self.primary, self.extra])
            upsert_product_ledger(path, self.record(purchase_cost=14))
            self.assertEqual(ledger_image_urls(next(read_ledger(path))[1]), [self.primary, self.extra])
            box = CatalogOutbox(folder)
            request = box.prepare(row, 'https://erp.example.com')
            client = Mock()
            client.connect.return_value = {'version': 1, 'authentication': 'none', 'upload_library': 'wff', 'initial_status': 'ready'}
            client.add.return_value = {'product_id': 'a' * 32, 'created': True, 'replayed': False}
            box.send(request, 'https://erp.example.com', lambda _: client)
            self.assertEqual(client.add.call_args.args[0]['variants'][0]['images'], [self.primary, self.extra])
            self.assertEqual(product_data(row, 'namespace', [])['variants'][0]['images'], [])

    def test_historical_backfill_preserves_business_fields_and_makes_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / 'ledger.xlsx'
            upsert_product_ledger(path, self.record(purchase_cost=12.5))
            original = path.read_bytes()
            self.submission(directory)
            result = backfill_ledger_image_links(path, directory)
            self.assertEqual(result['updated'], 1)
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            _, row = next(read_ledger(path))
            self.assertEqual(row['purchase_cost'], 12.5)
            self.assertEqual(ledger_image_urls(row), [self.primary, self.extra])
            self.assertEqual(backfill_ledger_image_links(path, directory)['updated'], 0)

    def test_wrong_task_or_shop_and_ambiguous_legacy_file_are_not_used(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / 'ledger.xlsx'
            upsert_product_ledger(path, self.record())
            for mismatch in ({'task_id': 999}, {'shop_config_id': 'wrong-shop'}, {'submitted': False}):
                self.submission(directory, **mismatch)
                self.assertEqual(backfill_ledger_image_links(path, directory)['updated'], 0)
            upsert_product_ledger(path, self.record(shop_config_id='shop-2'))
            self.submission(directory)
            self.assertEqual(backfill_ledger_image_links(path, directory)['updated'], 0)
            self.submission(directory, shop_config_id='shop-2')
            self.assertEqual(backfill_ledger_image_links(path, directory)['updated'], 1)
            rows = [row for _, row in read_ledger(path)]
            self.assertEqual(ledger_image_urls(rows[0]), [])
            self.assertEqual(ledger_image_urls(rows[1]), [self.primary, self.extra])

    def test_local_paths_are_not_saved_as_public_links_and_invalid_json_is_reported(self):
        self.assertEqual(image_link_fields('C:/private.jpg', [])['primary_image_url'], '')
        with self.assertRaises(ValueError):
            ledger_image_urls({'image_urls_json': '{}'})
