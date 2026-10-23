import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests
from openpyxl import Workbook
from erp_ledger_legacy import draft_from_row, import_ledger, read_ledger


def ledger_file(folder):
    path = Path(folder) / 'ledger.xlsx'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '产品台账'
    sheet.append(['货号', '商品标题', '采购成本', '售价', '包装毛重(g)', '上品店铺'])
    sheet.append(['TEST-1', 'Product', None, 99, 500, 'Shop'])
    sheet.append(['TEST-2', None, None, None, None, 'Shop'])
    workbook.save(path)
    workbook.close()
    return path


class LedgerImportTests(unittest.TestCase):
    def test_empty_fields_and_measurements_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            path = ledger_file(folder)
            before = path.read_bytes()
            rows = list(read_ledger(path))
            payload = draft_from_row(rows[0][1], 'stable-id')
            self.assertIsNone(payload['purchase_price_cny'])
            self.assertIsNone(payload['description_category_id'])
            self.assertIsNone(payload['package_weight_g'])
            self.assertEqual(payload['source_snapshot']['ledger_import']['row']['gross_weight_g'], 500)
            self.assertEqual(payload['status'], 'draft')
            self.assertEqual(path.read_bytes(), before)

    def test_create_duplicate_and_missing_title(self):
        with tempfile.TemporaryDirectory() as folder:
            path = ledger_file(folder)
            client = Mock(url='http://localhost:8788')
            response = Mock(status_code=201)
            response.json.return_value = {'id': 'product-id'}
            client.session.request.return_value = response
            first = import_ledger(path, folder, client)
            self.assertEqual([i['status'] for i in first['items']], ['created', 'failed'])
            second = import_ledger(path, folder, client)
            self.assertEqual(second['items'][0]['status'], 'duplicate')
            self.assertEqual(client.session.request.call_count, 1)

    def test_timeout_never_reposts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = ledger_file(folder)
            client = Mock(url='http://localhost:8788')
            client.session.request.side_effect = requests.Timeout('result unknown')
            import_ledger(path, folder, client)
            report = import_ledger(path, folder, client)
            self.assertEqual(client.session.request.call_count, 1)
            self.assertIn('禁止自动重复创建', report['items'][0]['error'])
