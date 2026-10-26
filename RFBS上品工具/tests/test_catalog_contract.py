import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from catalog_contract import public_url, validate_product, validate_receipt
from catalog_upload import CatalogOutbox, product_data
from shared_catalog_client import CatalogClient, CatalogRequestError, retry_after_timestamp


CAPABILITY = {'version': 1, 'authentication': 'none', 'upload_library': 'wff', 'initial_status': 'ready'}
RECEIPT = {'product_id': 'a' * 32, 'created': True, 'replayed': False}


def payload():
    return {'source': 'ozon-seller-center', 'external_id': 'stable-install:shop:product',
            'sku': 'PRODUCT', 'name': 'Product', 'variants': [{'sku': 'VARIANT'}]}


def response(value, status=200, headers=None):
    return Mock(status_code=status, json=Mock(return_value=value), headers=headers or {})


class ContractTests(unittest.TestCase):
    def test_minimal_and_complete_product_with_multiple_variants(self):
        value = payload()
        validate_product(value)
        value.update(supplier='Supplier', source_url='', description_category_id=None, type_id=2)
        value['variants'][0].update(specification='Blue 30x180cm', color='Blue', size='30x180cm',
            purchase_price_cny=0, package_length_mm=300, package_width_mm=200,
            package_height_mm=20, weight_grams=180, images=['https://example.com/a.jpg'])
        value['variants'].append({'sku': 'OTHER', 'purchase_price_cny': None, 'images': []})
        validate_product(value)

    def test_forbidden_response_and_platform_fields_never_reach_post(self):
        for field in ('id', 'source_type', 'status', 'updated_at', 'price', 'stock', 'password'):
            value = {**payload(), field: 'forbidden'}
            session = Mock()
            client = CatalogClient('https://erp.example.com', session)
            with self.subTest(field=field), self.assertRaises(ValueError):
                client.add(value)
            session.request.assert_not_called()

    def test_string_numeric_and_collection_limits(self):
        invalid = []
        for field in ('source', 'external_id', 'sku', 'name', 'supplier', 'source_url'):
            invalid.append({**payload(), field: None})
        invalid.extend([{**payload(), 'source': 'Bad source'}, {**payload(), 'supplier': 'x' * 121},
                        {**payload(), 'name': 'x' * 601}, {**payload(), 'variants': []},
                        {**payload(), 'variants': [{'sku': str(i)} for i in range(101)]},
                        {**payload(), 'variants': [{'sku': ' A '}, {'sku': 'A'}]},
                        {**payload(), 'type_id': 1.5}, {**payload(), 'type_id': True}])
        for field, value in (('specification', None), ('color', 'x' * 201), ('size', None),
                             ('weight_grams', 0), ('weight_grams', float('inf')),
                             ('purchase_price_cny', -1), ('purchase_price_cny', True),
                             ('purchase_price_cny', float('nan')), ('price', 12),
                             ('images', ['https://example.com/a.jpg'] * 2), ('images', [''])):
            invalid.append({**payload(), 'variants': [{'sku': 'VARIANT', field: value}]})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_product(value)

    def test_public_link_contract(self):
        for value in ('https://example.com/a.jpg', 'https://example.com:443/a.jpg', 'https://8.8.8.8/a.jpg'):
            self.assertEqual(public_url(value), value)
        invalid = ['http://example.com/a', 'https://example.com:8443/a', 'https://localhost/a',
                   'https://host/a', 'https://test.localhost/a', 'https://127.0.0.1/a',
                   'https://10.1.2.3/a', 'https://[::1]/a', 'https://192.0.2.1/a',
                   'https://224.0.0.1/a', 'https://user:pass@example.com/a',
                   'https://example.com/a?token=x', 'https://example.com/a#photo',
                   'https://example.com/a?', 'https://example.com/a#', 'https://example.com/a b',
                   'https://example.com/' + 'a' * 4000]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_url(value)

    def test_ledger_mapping_keeps_identity_optional_source_and_units(self):
        row = {'shop_config_id': 'shop', 'offer_id': 'SKU', 'title': 'Title', 'model_name': 'Model',
               'supplier_1688_url': '测试', 'purchase_cost': 0, 'depth_mm': 300, 'gross_weight_g': 180,
               'supplier': 'Supplier', 'color': 'Blue', 'size': 'S', 'category_id': 1.0}
        result = product_data(row, 'install')
        self.assertNotIn('source_url', result)
        self.assertNotIn('source_type', result)
        self.assertNotIn('status', result)
        self.assertEqual(result['variants'][0]['package_length_mm'], 300)
        self.assertEqual(result['variants'][0]['weight_grams'], 180)
        self.assertEqual(result['variants'][0]['color'], 'Blue')
        self.assertEqual(result['supplier'], 'Supplier')
        self.assertEqual(result['description_category_id'], 1)
        from hashlib import sha256
        expected = sha256(json.dumps(['install', 'Ozon', 'shop', 'SKU'], ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(result['external_id'], expected)
        for value in ('1e9999', 'not-a-number'):
            with self.assertRaises(ValueError):
                product_data({**row, 'purchase_cost': value}, 'install')

    def test_receipt_requires_created_or_replayed(self):
        validate_receipt(RECEIPT)
        validate_receipt({**RECEIPT, 'created': False, 'replayed': True})
        for value in ({'product_id': 'a' * 32}, {**RECEIPT, 'replayed': True},
                      {**RECEIPT, 'created': 'true'}, {**RECEIPT, 'product_id': 'INVALID'}):
            with self.assertRaises(ValueError):
                validate_receipt(value)


class ClientTests(unittest.TestCase):
    def test_wff_capability_then_post_without_adding_server_fields(self):
        session = Mock()
        session.request.side_effect = [response(CAPABILITY), response(RECEIPT)]
        client = CatalogClient('https://erp.example.com', session)
        value = payload()
        before = copy.deepcopy(value)
        self.assertEqual(client.add(value), RECEIPT)
        self.assertEqual(value, before)
        methods = [call.args[0] for call in session.request.call_args_list]
        self.assertEqual(methods, ['GET', 'POST'])
        self.assertEqual(session.request.call_args.kwargs['json'], value)
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])

    def test_old_server_is_not_written_to_and_unknown_read_libraries_are_retained(self):
        session = Mock()
        session.request.return_value = response({'version': 1, 'authentication': 'none'})
        client = CatalogClient('https://erp.example.com', session)
        with self.assertRaisesRegex(ValueError, 'wff/ready'):
            client.add(payload())
        self.assertEqual(session.request.call_count, 1)
        session.request.return_value = response({'id': 'a' * 32, 'source_type': 'future-library'})
        self.assertEqual(client.read('a' * 32)['source_type'], 'future-library')

    def test_safe_trace_http_error_and_unknown_network_result(self):
        session = Mock()
        session.request.return_value = response({'message': 'SECRET', 'trace_id': 'trace-123'}, 409)
        client = CatalogClient('https://erp.example.com', session)
        with self.assertRaises(CatalogRequestError) as captured:
            client.connect()
        self.assertEqual(captured.exception.status_code, 409)
        self.assertIn('trace-123', str(captured.exception))
        self.assertNotIn('SECRET', str(captured.exception))
        session.request.side_effect = requests.Timeout('SECRET')
        with self.assertRaises(CatalogRequestError) as captured:
            client.connect()
        self.assertNotIn('SECRET', str(captured.exception))

    def test_rate_limit_cooldown_preserves_original_and_stops_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            box = CatalogOutbox(folder)
            row = {'shop_config_id': 'shop', 'offer_id': 'SKU', 'title': 'Title'}
            path = box.prepare(row, 'https://erp.example.com')
            original = path.read_bytes()
            other = box.prepare({**row, 'offer_id': 'OTHER'}, 'https://erp.example.com')
            client = Mock()
            client.connect.return_value = CAPABILITY
            client.add.side_effect = CatalogRequestError(429, retry_at=1060)
            with patch('catalog_upload.time.time', return_value=1000):
                with self.assertRaises(CatalogRequestError):
                    box.send(path, 'https://erp.example.com', lambda _: client)
                factory = Mock()
                with self.assertRaises(CatalogRequestError):
                    box.send(other, 'https://erp.example.com', factory)
                factory.assert_not_called()
            self.assertEqual(path.read_bytes(), original)
            with patch.object(box, 'send', side_effect=CatalogRequestError(429)) as send:
                box.retry('https://erp.example.com', Mock())
                self.assertEqual(send.call_count, 1)

    def test_historical_receipt_remains_authoritative_without_reupload(self):
        with tempfile.TemporaryDirectory() as folder:
            box = CatalogOutbox(folder)
            path = box.prepare({'shop_config_id': 'shop', 'offer_id': 'SKU', 'title': 'Title'}, 'https://erp.example.com')
            receipt = path.with_name(path.name.replace('.request.json', '.receipt.json'))
            receipt.write_text(json.dumps(RECEIPT), encoding='utf-8')
            factory = Mock()
            self.assertEqual(box.send(path, 'https://erp.example.com', factory), RECEIPT)
            factory.assert_not_called()

    def test_missing_namespace_cannot_create_new_identity_for_existing_uploads(self):
        with tempfile.TemporaryDirectory() as folder:
            box = CatalogOutbox(folder)
            row = {'shop_config_id': 'shop', 'offer_id': 'SKU', 'title': 'Title'}
            path = box.prepare(row, 'https://erp.example.com')
            original = path.read_bytes()
            (box.root / 'namespace.json').unlink()
            with self.assertRaisesRegex(ValueError, '不能重新生成身份'):
                box.prepare(row, 'https://erp.example.com')
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse((box.root / 'namespace.json').exists())

    def test_retry_after_seconds_and_http_date(self):
        with patch('shared_catalog_client.time.time', return_value=1000):
            self.assertEqual(retry_after_timestamp('60'), 1060)
            self.assertEqual(retry_after_timestamp('Thu, 01 Jan 1970 00:18:00 GMT'), 1080)
            self.assertIsNone(retry_after_timestamp('invalid'))
