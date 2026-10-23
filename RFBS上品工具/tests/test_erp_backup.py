import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from core import load_json, atomic_write_json
from erp_backup import BackupOutbox, ErpBackupClient, base_url


def fixture():
    item = dict(offer_id='TEST-001', name='Test product', primary_image='https://images.example.com/main.jpg',
        images=['https://images.example.com/detail.jpg'], price='99', old_price='129', currency_code='CNY',
        description_category_id=17028709, type_id=115947437, depth=20, width=10, height=5,
        dimension_unit='cm', weight=.5, weight_unit='kg', attributes=[{'id': 85, 'values': [{'value': 'Blue', 'dictionary_value_id': 123}]}],
        complex_attributes=[{'attributes': [{'id': 100, 'complex_id': 42, 'values': [{'value': 'test'}]}]}])
    submitted = dict(item=item, submitted=True, import_result={'result': {'items': [
        {'offer_id': 'TEST-001', 'status': 'imported', 'product_id': 900000000001, 'errors': []}]}},
        content=dict(description_ru='Description', tags_ru=['test']))
    ledger = dict(shop_config_id='store-1', shop_name='Shop', purchase_cost='10.5', label_fee='2', model_name='MODEL')
    return submitted, ledger


class BackupTests(unittest.TestCase):
    def test_disabled_backup_ignores_even_enabled_legacy_config(self):
        from app import RfbsListingApp
        app = object.__new__(RfbsListingApp)
        app._log = Mock()
        app._save_auto_jobs = Mock()
        app._product_ledger_record = Mock(side_effect=OSError('simulated backup disk error'))
        job = {'id': 'task'}
        with patch('app.load_json', return_value={'enabled': True, 'base_url': 'http://localhost:8788'}):
            app._backup_to_product_library('unused.json', {}, {}, 1, 1, 1, [], job)
        self.assertEqual(job, {'id': 'task'})
        app._product_ledger_record.assert_not_called()
        app._save_auto_jobs.assert_not_called()
        app._log.assert_not_called()

    def test_durable_identity_revision_and_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            box = BackupOutbox(folder)
            submitted, ledger = fixture()
            first = box.prepare(submitted, ledger, [], 'job-1', 'http://127.0.0.1:8788')
            raw = first.read_bytes()
            submitted['item']['price'] = '100'
            self.assertEqual(box.prepare(submitted, ledger, [], 'job-1', 'http://127.0.0.1:8788').read_bytes(), raw)
            atomic_write_json(first.with_suffix('.receipt.json'), {'imported': True})
            second = box.prepare(submitted, ledger, [], 'job-1', 'http://127.0.0.1:8788')
            a, b = json.loads(raw), load_json(second, {})
            self.assertEqual(a['producer'], b['producer'])
            self.assertEqual(a['products'][0]['external_product_id'], b['products'][0]['external_product_id'])
            self.assertEqual(b['products'][0]['revision'], 2)
            self.assertEqual(b['products'][0]['variants'][0]['package']['weight_g'], 500)
            self.assertEqual(b['products'][0]['variants'][0]['package']['length_mm'], 200)

    def test_success_and_timeout_reuses_exact_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            submitted, ledger = fixture()
            path = BackupOutbox(folder).prepare(submitted, ledger, [], 'job', 'http://localhost:8788')
            session = Mock(headers={})
            def response(data, status=200):
                result = Mock(status_code=status)
                result.json.return_value = data
                return result
            receipt = dict(imported=True, export_id=path.stem, items=[{'product_id': 'erp-1'}])
            session.request.side_effect = [response({'multiuser': False}), response({'valid': True, 'can_import': True}), requests.Timeout(), response(receipt)]
            client = ErpBackupClient('http://localhost:8788', token='DO-NOT-EXPORT', session=session, sleep=lambda _: None)
            client.upload(path)
            calls = session.request.call_args_list
            self.assertEqual(calls[1].kwargs['data'], calls[2].kwargs['data'])
            self.assertEqual(calls[2].kwargs['data'], calls[3].kwargs['data'])
            self.assertNotIn(b'DO-NOT-EXPORT', path.read_bytes())
            self.assertTrue(path.with_suffix('.receipt.json').exists())
            self.assertFalse(calls[3].kwargs['allow_redirects'])

    def test_reject_unsuccessful_and_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            submitted, ledger = fixture()
            submitted['import_result']['result']['items'][0]['status'] = 'processing'
            with self.assertRaises(ValueError):
                BackupOutbox(folder).prepare(submitted, ledger, [], 'job', 'http://localhost:8788')
            submitted, ledger = fixture()
            submitted['item']['api_key'] = 'secret'
            with self.assertRaises(ValueError):
                BackupOutbox(folder).prepare(submitted, ledger, [], 'job', 'http://localhost:8788')
        for url in ('http://example.com', 'https://user:pass@example.com', 'http://localhost:8788/path'):
            with self.assertRaises(ValueError):
                base_url(url)

    def test_conflict_and_401_do_not_import(self):
        for status in (200, 401, 422):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                submitted, ledger = fixture()
                path = BackupOutbox(folder).prepare(submitted, ledger, [], 'job', 'http://localhost:8788')
                session = Mock(headers={})
                ok = Mock(status_code=200)
                ok.json.return_value = {'multiuser': False}
                bad = Mock(status_code=status)
                bad.json.return_value = {'valid': True, 'can_import': False, 'code': 'conflict'}
                session.request.side_effect = [ok, bad]
                with self.assertRaises((ValueError, RuntimeError)):
                    ErpBackupClient('http://localhost:8788', 'test', session=session).upload(path)
                self.assertEqual(session.request.call_count, 2)
                self.assertFalse(path.with_suffix('.receipt.json').exists())


@unittest.skipUnless(os.environ.get('ERP_RUN_LOCAL_CONTRACT') == '1', 'Local ERP contract integration is opt-in')
class LocalErpContractTests(unittest.TestCase):
    def test_real_erp_import_and_replay_in_isolated_database(self):
        with tempfile.TemporaryDirectory() as folder:
            os.environ.update(ERP_DATA_DIR=folder, ERP_DATABASE_URL='sqlite:///' + (Path(folder) / 'erp.db').as_posix(),
                ERP_ACCESS_TOKEN='local-contract-test-only', ERP_MULTIUSER_ENABLED='false',
                ERP_ASSET_REMOTE_ALLOWED_HOSTS='["images.example.com"]')
            from fastapi.testclient import TestClient
            from erp.core.config import get_settings
            get_settings.cache_clear()
            from erp.main import app
            from erp.core.database import engine
            from erp.models import Base
            Base.metadata.create_all(engine)
            with TestClient(app) as http:
                class SessionAdapter:
                    headers = {}
                    def request(self, method, url, **kwargs):
                        kwargs.pop('allow_redirects', None)
                        return http.request(method, url, follow_redirects=False, headers={**self.headers, **kwargs.pop('headers', {})}, **kwargs)
                submitted, ledger = fixture()
                path = BackupOutbox(Path(folder) / 'outbox').prepare(submitted, ledger, [], 'job-1', 'http://localhost:8788')
                session = SessionAdapter()
                client = ErpBackupClient('http://localhost:8788', token='local-contract-test-only', session=session)
                result = client.upload(path)
                self.assertTrue(result['imported'])
                self.assertFalse(result['copy_ready'])
                self.assertFalse(result['images_verified'])
                response = http.get('/api/v1/catalog/follow-backups/products/' + result['items'][0]['product_id'], headers=session.headers)
                self.assertEqual(response.status_code, 200)
                replay = client.call('POST', '/api/v1/catalog/follow-backups/import', data=path.read_bytes(), headers={'Content-Type': 'application/json'})
                self.assertTrue(replay['replayed'])
                submitted['item']['price'] = '100'
                updated = BackupOutbox(Path(folder) / 'outbox').prepare(submitted, ledger, [], 'job-1', 'http://localhost:8788')
                revised = client.upload(updated)
                self.assertEqual(revised['items'][0]['product_id'], result['items'][0]['product_id'])
                self.assertEqual(revised['counts']['update'], 1)
                old = load_json(path, {})
                old['export_id'] = 'old-revision-test'
                rejected = http.post('/api/v1/catalog/follow-backups/import', json=old, headers=session.headers)
                self.assertEqual(rejected.status_code, 409)
                from test_erp_ledger import ledger_file
                from erp_ledger_legacy import import_ledger
                ledger_path = ledger_file(folder)
                imported = import_ledger(ledger_path, Path(folder) / 'ledger-outbox', client)
                self.assertEqual(imported['items'][0]['status'], 'created', imported)
                self.assertEqual(imported['items'][1]['status'], 'failed')
                draft = http.get('/api/v1/catalog/products/' + imported['items'][0]['product_id'], headers=session.headers).json()
                self.assertEqual(draft['status'], 'draft')
                self.assertIsNone(draft['purchase_price_cny'])
                self.assertIsNone(draft['package_weight_g'])
                repeated = import_ledger(ledger_path, Path(folder) / 'ledger-outbox', client)
                self.assertEqual(repeated['items'][0]['status'], 'duplicate')
                from erp_incremental import IncrementalOutbox
                incremental = IncrementalOutbox(Path(folder) / 'incremental')
                a = dict(platform='Ozon', listing_mode='跟卖', offer_id='A-SKU', shop_config_id='A', shop_name='A',
                         title='First title', purchase_cost='0', price='99', currency_code='CNY', gross_weight_g=355,
                         depth_mm=200, width_mm=200, height_mm=200, dimension_unit='mm', weight_unit='g')
                b = dict(a, offer_id='B-SKU', shop_config_id='B', shop_name='B', title='Second title', price='100')
                target = 'http://localhost:8788'
                path_a = incremental.prepare_row(a, {'product_group_id': 'group-ab', 'base_offer_id': 'base'}, target)
                path_b = incremental.prepare_row(b, {'product_group_id': 'group-ab', 'base_offer_id': 'base'}, target)
                result_a = client.upload(path_a)
                result_b = client.upload(path_b)
                pid = result_a['items'][0]['product_id']
                self.assertEqual(pid, result_b['items'][0]['product_id'])
                self.assertTrue(result_b['items'][0]['field_conflicts'])
                snapshot = client.call('GET', '/api/v1/catalog/follow-backups/products/' + pid)
                self.assertEqual(len(snapshot['snapshot']['product']['listings']), 2)
                variant = snapshot['snapshot']['product']['variants'][0]
                self.assertEqual(variant['package']['weight_g'], 355)
                self.assertEqual(variant['package']['length_mm'], 200)
                self.assertEqual(variant['cost']['purchase'], '0.0000')
                minimal = incremental.prepare_row({'_ledger_identity': 'empty-row'}, {}, target)
                self.assertTrue(client.upload(minimal)['imported'])
                creative_results = []
                for store in ('A', 'B'):
                    full, full_ledger = fixture()
                    full['item']['offer_id'] = store + '-creative'
                    full['item']['name'] = store + ' creative title'
                    full['item']['primary_image'] = 'https://images.example.com/' + store + '.jpg'
                    full['import_result']['result']['items'][0]['offer_id'] = full['item']['offer_id']
                    full_ledger.update(shop_config_id=store, shop_name=store, title=full['item']['name'],
                        offer_id=full['item']['offer_id'], price=full['item']['price'],
                        old_price=full['item']['old_price'], currency_code='CNY')
                    creative_path = incremental.prepare_row(full_ledger, {'product_group_id': 'creative-ab', 'base_offer_id': 'creative'}, target, full)
                    creative_results.append(client.upload(creative_path))
                self.assertEqual(creative_results[0]['items'][0]['product_id'], creative_results[1]['items'][0]['product_id'])
                creative_snapshot = client.call('GET', '/api/v1/catalog/follow-backups/products/' + creative_results[0]['items'][0]['product_id'])
                self.assertEqual(len(creative_snapshot['snapshot']['product']['listings']), 2)
                main_variant = creative_snapshot['snapshot']['product']['variants'][0]
                self.assertEqual(main_variant['content']['title'], 'A creative title')
            engine.dispose()
