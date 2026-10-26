import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from catalog_upload import CatalogOutbox, product_data, upload_after_listing
from core import load_json, atomic_write_json


def row():
    return dict(platform='Ozon', shop_config_id='shop-1', offer_id='A', title='Товар', purchase_cost='12.5',
        depth_mm='100', width_mm='200', height_mm='30', gross_weight_g='150', category_id=1, type_id=2,
        description='local only', attributes_json='local only', price='999', password='NEVER-SEND')


class CatalogUploadTests(unittest.TestCase):
    def test_placeholder_or_missing_supplier_url_does_not_block_upload(self):
        for placeholder in ('测试', '', None, '   ', 'https://user:pass@example.com/a'):
            with self.subTest(placeholder=placeholder):
                data = {**row(), 'supplier_1688_url': placeholder}
                result = product_data(data, 'instance')
                self.assertNotIn('source_url', result)
                self.assertEqual(data['supplier_1688_url'], placeholder)
                self.assertEqual(result['sku'], 'A')

    def test_source_falls_back_to_ozon_without_tracking_parameters(self):
        data = {**row(), 'supplier_1688_url': '测试',
                'ozon_source_url': 'https://www.ozon.ru/product/item-123/?sh=abc&abt_att=1#photos'}
        self.assertEqual(product_data(data, 'instance')['source_url'],
                         'https://www.ozon.ru/product/item-123/')
        data['supplier_1688_url'] = 'http://detail.1688.com/offer/123.html?spm=tracking'
        self.assertEqual(product_data(data, 'instance')['source_url'],
                         'https://detail.1688.com/offer/123.html')
        data.update(supplier_1688_url='', ozon_source_url='测试')
        self.assertNotIn('source_url', product_data(data, 'instance'))

    def test_placeholder_reaches_upload_and_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            box = CatalogOutbox(folder)
            path = box.prepare({**row(), 'supplier_1688_url': '测试'}, 'https://erp.example.com')
            client = Mock()
            client.connect.return_value = {'version': 1, 'authentication': 'none', 'upload_library': 'wff', 'initial_status': 'ready'}
            client.add.return_value = {'product_id': 'a' * 32, 'created': True, 'replayed': False}
            self.assertEqual(box.send(path, 'https://erp.example.com', lambda _: client)['product_id'], 'a' * 32)
            self.assertNotIn('source_url', client.add.call_args.args[0])

    def test_whitelist_units_unknown_cost_and_no_private_snapshot(self):
        value = product_data(row(), 'instance', ['https://images.example.com/a.jpg'])
        self.assertEqual(value['variants'][0]['purchase_price_cny'], 12.5)
        self.assertEqual(value['variants'][0]['weight_grams'], 150)
        self.assertNotIn('description', value)
        self.assertNotIn('password', value)
        self.assertNotIn('price', value)
        missing = row()
        missing.pop('purchase_cost')
        self.assertIsNone(product_data(missing, 'instance')['variants'][0]['purchase_price_cny'])
        self.assertNotEqual(value['external_id'], product_data(row(), 'other-instance')['external_id'])

    def test_invalid_urls_and_numbers_stop_before_upload(self):
        for images in (['https://a.test/a?token=x'], ['file:///a'], ['https://user:pass@a.test/a']):
            with self.assertRaises(ValueError):
                product_data(row(), 'instance', images)
        for cost in ('NaN', 'Infinity', '-1'):
            with self.assertRaises(ValueError):
                product_data({**row(), 'purchase_cost': cost}, 'instance')

    def test_durable_original_retry_and_receipt_prevent_reupload(self):
        with tempfile.TemporaryDirectory() as folder:
            box = CatalogOutbox(folder)
            path = box.prepare(row(), 'https://erp.example.com')
            original = path.read_bytes()
            client = Mock()
            client.connect.return_value = {'version': 1, 'authentication': 'none', 'upload_library': 'wff', 'initial_status': 'ready'}
            client.add.side_effect = RuntimeError('timeout')
            factory = Mock(return_value=client)
            with self.assertRaises(RuntimeError):
                box.send(path, 'https://erp.example.com', factory)
            self.assertEqual(box.records()[0]['status'], '待补传 / 结果未确认')
            self.assertEqual(box.records()[0]['product_id'], '')
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(box.prepare(row(), 'https://erp.example.com'), path)
            client.add.side_effect = None
            client.add.return_value = {'product_id': 'a' * 32, 'created': True, 'replayed': False}
            box.send(path, 'https://erp.example.com', factory)
            self.assertEqual(box.records()[0]['product_id'], 'a' * 32)
            self.assertEqual(box.records()[0]['status'], '上传成功（有服务端回执）')
            self.assertEqual(client.add.call_count, 2)
            box.send(path, 'https://erp.example.com', factory)
            self.assertEqual(client.add.call_count, 2)
            with self.assertRaises(ValueError):
                box.prepare({**row(), 'title': 'changed'}, 'https://erp.example.com')
            with self.assertRaises(ValueError):
                box.send(path, 'https://different.example.com', factory)

    def test_bad_capability_never_posts_and_failed_data_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as folder:
            box = CatalogOutbox(folder)
            path = box.prepare(row(), 'https://erp.example.com')
            client = Mock()
            client.connect.return_value = {'version': 1, 'authentication': 'cookie'}
            with self.assertRaises(ValueError):
                box.send(path, 'https://erp.example.com', lambda _: client)
            client.add.assert_not_called()
            atomic_write_json(Path(folder) / 'shared-catalog-settings.json', {'enabled': True, 'base_url': 'https://erp.example.com'})
            log = Mock()
            with patch('catalog_upload.CatalogOutbox.send', side_effect=RuntimeError('server unavailable')):
                upload_after_listing(folder, row(), [], log)
            self.assertTrue(log.called)
            self.assertTrue(list(box.root.glob('*.request.json')))

    def test_disabled_upload_does_not_create_outbox(self):
        with tempfile.TemporaryDirectory() as folder:
            upload_after_listing(folder, row(), [], Mock())
            self.assertFalse((Path(folder) / 'shared-catalog-outbox').exists())

    def test_app_backup_hook_never_propagates_preparation_failure(self):
        from app import RfbsListingApp
        app = RfbsListingApp.__new__(RfbsListingApp)
        app._product_ledger_record = Mock(side_effect=OSError('disk error'))
        app._log = Mock()
        with patch('app.load_json', return_value={'enabled': True}):
            app._backup_to_product_library(None, {}, {}, 1, 2, 3, [])
        app._log.assert_called_once()
